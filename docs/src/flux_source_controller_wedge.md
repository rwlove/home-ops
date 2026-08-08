# Flux source-controller wedge: `Recreate` + a slow image pull

Runbook for a cluster-wide GitOps outage where **every** HelmRelease
fails with `ArtifactFailed` and most Kustomizations go
`DependencyNotReady`. Captured 2026-08-08 from a live incident.

The short version: `source-controller`'s Deployment uses
`strategy: Recreate`. If its replacement pod stalls on an image pull,
kubelet cannot reap the old pod, `Recreate` refuses to create a
successor, and the controller that serves chart and Git artifacts to
the whole cluster stays at zero replicas.

## Symptoms

- `HelmRelease` objects across unrelated namespaces report
  `ArtifactFailed` with `dial tcp <source-controller-clusterip>:80:
  connect: connection refused`.
- The root `Kustomization` reports `ArtifactFailed`
  (`failed to download archive: GET
  http://source-controller.flux-system.svc.cluster.local./gitrepository/...`),
  and everything downstream cascades to `DependencyNotReady`.
- `flux-instance` lands in `RollbackFailed`:
  `timeout waiting for: [FluxInstance/flux-system/flux status: 'InProgress']`.
- Nodes are all `Ready`, no resource pressure, etcd has quorum,
  Ceph is `HEALTH_OK`. Running pods keep serving — this is a
  **control-plane** outage, not a data-plane one.

## Confirming it

```sh
kubectl get deploy -n flux-system source-controller
# READY 0/1   UP-TO-DATE 0   AVAILABLE 0

kubectl get rs -n flux-system -l app=source-controller \
  -o custom-columns='NAME:.metadata.name,DESIRED:.spec.replicas'
# every ReplicaSet DESIRED=0  <-- the tell

kubectl get pods -n flux-system -l app=source-controller
# one pod, Terminating or Pending, stuck in PodInitializing
```

Two details that mislead:

- The Deployment's `Progressing` condition reads
  `True / NewReplicaSetAvailable — "has successfully progressed"`
  while `Available` is `False / MinimumReplicasUnavailable`. Trust
  `Available`.
- `kubectl describe pod` shows all init containers `Completed` and the
  app container `Pulling image ...` with no further events. There is no
  error, no backoff, no `ImagePullBackOff` — it simply never finishes.
  A pull that is *slow* looks identical to one that is *stuck*; you
  cannot tell them apart from the API (that needs `crictl` on the node).

The pod's `terminationGracePeriodSeconds` will be long exceeded with no
finalizers set. Kubelet is blocked on the in-flight pull.

## The fix

Force-delete the stalled pod. It is the only thing holding the
`Recreate` barrier shut:

```sh
kubectl delete pod -n flux-system <stuck-source-controller-pod> \
  --force --grace-period=0
```

The Deployment immediately scales up the **previous-revision**
ReplicaSet, whose image is already present on the node, so it comes
back in seconds with no pull. Verified: `1/1 Running` in 8 s.

This is safe — `source-controller` is stateless, mounts no PVC, and the
container never started, so nothing was mid-write.

Then let the rest unwind on its own:

1. All Flux **sources** (`OCIRepository` / `HelmRepository` /
   `GitRepository`) go `Ready` within a reconcile as artifacts start
   serving again.
2. The root `Kustomization` keeps a **latched** `ArtifactFailed` and
   retries on its `spec.interval` (10m here). To skip the wait:

   ```sh
   kubectl annotate kustomization -n flux-system home-ops-kubernetes \
     reconcile.fluxcd.io/requestedAt="$(date +%s)" --overwrite
   ```

3. Downstream Kustomizations cascade back to `Ready` over the next few
   cycles.
4. `flux-instance` clears itself once `FluxInstance` health passes. It
   needs no manual reconcile — its `RollbackFailed` was a *symptom* of
   the health check timing out, not an independent failure.

## Why it escalates

`source-controller` is the single dependency of every HelmRelease and
Kustomization in the cluster, so a few minutes of downtime becomes a
total reconcile stall. In the observed incident: **161 of 233
Kustomizations** and **18 of 170 HelmReleases** were failing at peak.

The `flux-instance` HelmRelease then makes it worse. Its
`upgrade.remediation` fires on the Helm timeout and attempts a
rollback; the rollback *also* waits on `FluxInstance` health, which is
still blocked on `source-controller`, so it fails too. The release ends
in `RollbackFailed` with its retries spent.

## Root trigger: bulk image-update waves

The stall was caused by registry contention. **46 image-update commits
merged in ~40 minutes**, so many nodes pulled many new images at once.
Pulls that normally take seconds took **5–19 minutes**:

| Component | Pull duration |
|---|---|
| `source-controller` v1.9.4 | 290 s |
| A document-management StatefulSet | 1117 s (18.6 min) |

Helm's default 5-minute timeout is well inside that window, so the
timeout trips, remediation spawns a replacement pod, that pod starts
its own pull, and the contention feeds itself.

**The queue does drain if left alone.** Every intervention costs a fresh
pull competing with the ones already in flight. During recovery the
backlog went 16 → 12 → 7 → 3 → 2 → 0 stuck pulls with no action taken,
and several HelmReleases cleared themselves without a reconcile.

## Related trap: the orphaned rollback pod

The same wave wedged two StatefulSets (a music service and a
document-management app) by a related mechanism:

1. HelmRelease upgrades the image; the new pod starts pulling.
2. Helm times out; the StatefulSet template is **rolled back** to the
   old image (`currentRevision == updateRevision`, old tag).
3. The **new-image pod persists**, unready, and a StatefulSet will not
   replace a pod that isn't Ready. The app stays down indefinitely.

`kubectl delete pod --force --grace-period=0` is the unlock here too.
**Check whether the rolled-back image is actually on the node first** —
if it was garbage-collected, the replacement pod has to pull it and you
gain nothing but a restart. One of the two came back in 12 s (image
cached); the other took another 18 minutes (it wasn't).

## Prevention

- **Don't merge large dependency waves at once.** Batch them and let
  Flux settle between batches. This is the whole root cause.
- **Raise Helm timeouts on anything in the critical path.** A
  `spec.timeout` shorter than a realistic worst-case pull turns a delay
  into a wedge.
- **Reconsider `remediation` on `flux-instance`.** Rollback cannot help
  when the failure is "the new pod hasn't finished pulling" — it only
  adds churn. Compare the reasoning in
  [`.agents/instructions/workarounds.md`](https://github.com/rwlove/home-ops)
  and the existing guidance that rollback is unsafe for forward-only
  changes.
- **`strategy: Recreate` on a singleton, cluster-critical Deployment is
  the structural hazard.** It is set by the flux-operator's
  distribution manifests, not by this repo, so it can't simply be
  patched here — but knowing it explains why this failure mode is
  qualitatively worse for `source-controller` than for a
  RollingUpdate workload, where the old pod keeps serving.

## Observability gaps found during this incident

Both cost real diagnostic time and are worth fixing:

- **`node_network_receive_bytes_total` returns 0.0 for every
  Kubernetes node.** The device filter doesn't match their interfaces,
  so per-node network throughput is unmeasurable — exactly what you
  want when triaging a suspected bandwidth problem. Only the
  storage/gateway hosts report real numbers.
- **The MCP gateway runs in-cluster**, so the tooling used to inspect
  the cluster disappears during a cluster incident. Alertmanager's and
  Prometheus's HTTP APIs remained reachable throughout and are the
  reliable fallback:

  ```sh
  curl -s 'https://alertmanager.<domain>/api/v2/alerts?active=true&silenced=false&inhibited=false'
  curl -s --get 'https://prometheus.<domain>/api/v1/query' --data-urlencode 'query=up == 0'
  ```

## Diagnostic dead ends

Recorded so the next person doesn't re-walk them:

- **The pull-through registry cache was not involved.** Its pod was
  healthy and its access log showed only three client IPs — nodes pull
  from upstream registries directly, not through it.
- **It was not a single bad node.** A mid-wave snapshot showed all 8
  stalled pods on one worker, which looked conclusive; it was
  scheduling coincidence. Once the backlog drained, that node still
  completed its pulls, just ~3–4× slower than its peers. Worth a
  follow-up, but it was not the cause.
- **etcd leader churn on the first control-plane node** looked alarming
  (17 changes/hr vs 2 on its peers) but is a **known pre-existing
  condition** — see [master1 etcd-Disk Swap Plan](master1_etcd_disk_swap.md).
  Quorum held and apiserver p99 stayed at 38 ms throughout.

**Lesson:** don't diagnose node health from a single snapshot taken
while work is actively being scheduled. Wait for the queue to drain,
then look at what's left.
