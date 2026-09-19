# Failure-Mode Runbooks

Recurring, named failure modes and their recovery procedures. These are
cluster-generic runbooks — independent of any single application — for the
handful of issues that have bitten this cluster more than once. Each entry
follows the same shape: **Symptom → How to confirm → Root cause → Recovery
(and Prevention where it applies).**

> Salvaged 2026-09-19 from the retired HomeAIOps DoD doc, which was otherwise
> historical. The langgraph-specific "local inference path down" escalation
> runbook was dropped with that pipeline; the four below are still current.

## ExternalSecret-extract field present but empty

**Symptom.** A Secret populated by an ExternalSecret is missing
the value an app needs. `kubectl get secret … -o yaml` shows the
key exists; `... | base64 -d | wc -c` returns 0 or 1 bytes. The
ExternalSecret itself reports `SecretSynced` cleanly — there is
no operator-level error.

**How to confirm.**

```sh
LEN=$(kubectl get secret -n <ns> <secret> \
  -o jsonpath='{.data.<KEY>}' | base64 -d | wc -c)
echo "decoded length: $LEN bytes"
kubectl get externalsecret -n <ns> <name> \
  -o jsonpath='{.status.conditions[?(@.type=="Ready")]}'
```

If the decoded length is 0–1 bytes and the ExternalSecret is
`Ready=True`, the 1P field exists but is blank.

**Root cause.** The 1P item has a placeholder field with the
right name but no value behind it. `external-secrets-operator`
copies the empty string into the target Secret — it can't tell
the difference between "deliberately empty" and "operator forgot
to paste the value."

**Recovery.** Two paths:

1. *Fill the empty 1P field.* Open the 1P item, paste the value,
   force-sync:

   ```sh
   kubectl annotate externalsecret -n <ns> <name> \
     force-sync=$(date +%s) --overwrite
   ```

2. *Point at the field the secret already lives under.* If the
   value is already in a different 1P item under a different
   field name (e.g. an empty `ANTHROPIC_API_KEY` here while the
   live value is in another item's `anthropic_api_key`),
   re-wire the ExternalSecret's `dataFrom` + template to pull
   the live field. Single rotation surface beats duplicate
   copies. The canonical example for this repo is the langgraph
   Anthropic key fix in
   [#11923](https://github.com/rwlove/home-ops/pull/11923).

**Prevention.** When adding a new ExternalSecret template
variable that maps from a 1P field, verify the field is non-
empty before merging. A `wc -c` check is enough.

## Stuck CSI RBD unmap (Ceph PG ghost-stuck)

**Symptom.** Pod with a `ceph-block` PVC stuck `ContainerCreating`
for minutes with events:

```text
FailedMount  MountVolume.MountDevice failed: rpc error: code = Aborted
desc = an operation with the given Volume ID … already exists
```

Or, on the originating node, `rbd unmap /dev/rbdN` fails with
exit status 16 (EBUSY) despite **no userland processes** holding
the device (verified via `/proc/[0-9]*/maps`, `/proc/[0-9]*/fd`,
and `/sys/block/rbdN/holders/` all empty).

`ceph -s` may report a stale `SLOW_OPS` aggregate, but per-OSD
`dump_blocked_ops` (both via admin socket and via mon-routed
`ceph tell osd.X`) shows **zero blocked ops**. The aggregate is
the mgr's cached-but-not-decremented count from a prior real
event — misleading.

**Underlying issue.** A specific PG owning the rbd_header object
is "ghost-stuck": it can't be queried (`ceph pg N.M query` times
out), it's not in `pg dump_stuck`, but raw `rados stat
rbd_header.<id>` against it hangs. The OSD primary has cleared
its blocked-ops queue but the PG's per-object lock or watcher
state is wedged.

**How to confirm.**

```sh
# 1. From a mon pod, with the embedded keyring:
MON=$(kubectl get pod -n rook-ceph -l app=rook-ceph-mon -o name | head -1)
ARGS='--conf /etc/ceph/ceph.conf
      --keyring /etc/ceph/keyring-store/keyring
      -m "[v2:<mon-ip>:3300,v1:<mon-ip>:6789]"'   # adjust to your mons

# 2. Identify the image_id from the affected node's kernel state:
kubectl debug node/<node> -it=false --image=busybox:1.36 -- \
  cat /host/sys/bus/rbd/devices/<N>/image_id
# → e.g. 0e6ed03d95369c

# 3. Find the PG that owns the header:
kubectl exec -n rook-ceph "$MON" -c mon -- bash -c "
  ceph $ARGS osd map ceph-blockpool rbd_header.<image_id>
"
# → 'pg P.Q -> up [primary, ...] acting [primary, ...]'

# 4. Confirm the PG itself is unresponsive:
kubectl exec -n rook-ceph "$MON" -c mon -- bash -c "
  timeout 15 ceph $ARGS pg P.Q query
"
# → exit 124 (timeout) is the signature

# 5. Confirm OSDs say they're idle (rules out a real slow op):
kubectl exec -n rook-ceph "$MON" -c mon -- bash -c "
  ceph $ARGS tell osd.<primary> dump_blocked_ops
"
# → empty / {ops: []}

# 6. Confirm raw RADOS read of the object also hangs:
kubectl exec -n rook-ceph "$MON" -c mon -- bash -c "
  timeout 15 rados $ARGS -p ceph-blockpool stat rbd_header.<image_id>
"
```

**Recovery — propose-then-execute.** Three options, least-invasive first:

1. *Targeted blocklist of the watcher client* — works if the
   watcher is on a distinct krbd nonce. Pull `client_addr` from
   `/sys/bus/rbd/devices/N/client_addr` on the originating node,
   confirm it's not shared with healthy rbd mappings on the same
   node, then:

   ```sh
   ceph $ARGS osd blocklist add <addr>/<nonce> 3600
   ```

   Retry `rbd unmap` from the node — should release within
   seconds.

2. *Force PG re-peering via `ceph osd down <primary>`* — when
   blocklist doesn't help (PG state itself is stuck, not just a
   client lease). Marking the primary down triggers re-peering
   via the replica OSDs; the daemon self-restarts under Rook
   within ~30s; PGs primaried elsewhere are unaffected. The
   acting set must have at least min_size replicas remaining
   alive for safety (verify with `ceph osd tree`).

3. *Decouple via PVC delete-and-recreate (regenerable data
   only)* — for caches like ZOT where the data is rebuildable
   from upstream sources:

   - `kubectl scale statefulset/<app> --replicas=0`
   - `kubectl delete pvc <pvc-name>` (reclaim policy `Delete`
     on the storage class will GC the underlying RBD image)
   - `kubectl scale statefulset/<app> --replicas=1`

   This recovers the workload independently of the Ceph fix.
   The underlying PG-ghost-stuck issue remains and needs
   option 1 or 2 separately.

**Prevention.** The class of error appears tied to a prior
cluster-network event (worker3 + worker7 network degradation
on 2026-05-20) that left rbd watchers in a broken state on the
affected nodes.
Stabilizing node-level network reliability is the long-term fix.
Until then, document the rbd-device-to-image mapping so the
identification step (3) is faster.

**Operational follow-up.** This cluster has no `rook-ceph-tools`
pod deployed (`operator/helmrelease.yaml` lacks
`toolbox.enabled: true`). All ceph CLI work has to go through a
mon pod with explicit `--conf` / `--keyring` / `-m` flags as in
step 1. Adding the toolbox to the rook-ceph operator helmrelease
is a quality-of-life follow-up; the 2026-05-21 incident showed
the time cost of working without it.

## ZOT cold-start recovery — the secondary failure mode

This runbook section pairs with the one above. When the
underlying Ceph PG issue clears, ZOT itself doesn't immediately
recover — it goes through a long cold-start sequence that can
look like a fresh failure.

**Symptom.** After the Ceph PG re-peer succeeds (osd.2 came
back up, `rbd info` works again from a mon pod), `zot-0` enters
a `CrashLoopBackOff` pattern even though the underlying volume
is healthy. Each restart shows `parsing next repo` log lines
walking through hundreds of cached images, but the pod gets
killed by its liveness probe before reaching the end.

```sh
kubectl logs -n kube-system zot-0 --tail=5 | grep parsing
# → "parsing next repo 'quay.io/...': total=272, progress=233"
# Restart count climbs by 1 every ~30 s.
```

After this clears (post-parse + scrub + GC + retention all run
on first boot), the pod becomes `Ready` but **serves chart
manifests with 5-minute latency** for the next 30–60 min while
background ops still contend for I/O.

**How to confirm — phase 1 (parse loop).**

```sh
kubectl get pod -n kube-system zot-0
# RESTARTS climbing by 1/30s — fingerprint
kubectl logs -n kube-system zot-0 --previous --tail=10 | grep -c "parsing next repo"
# → many; the parse never completes inside the liveness window
kubectl get pod -n kube-system zot-0 -o jsonpath='{.spec.containers[0].livenessProbe}'
# → check failureThreshold; default is 3 × 10s = 30 s — too short
```

**How to confirm — phase 2 (post-parse latency).**

ZOT pod is `Ready=True`, but `helm template` against ZOT-mirrored
charts times out from CI:

```sh
kubectl logs -n kube-system zot-0 --tail=20 | grep '"latency"'
# Each manifest GET reports `"latency":"5m2s"` (or similar) —
# fingerprint of disk contention from scrub + gc + retention
kubectl logs -n kube-system zot-0 --tail=20 | grep -E "scrub|garbage collected|retention"
# All three running concurrently on cold-start
```

**Root cause.** ZOT v2 runs three periodic maintenance ops:

- *Scrub* — manifest/blob integrity check
- *GC* — remove unreferenced blobs
- *Retention* — delete tags per retention policy

On a cold restart these all fire at once. With a 500 GiB
ceph-block PVC and 272 cached repos, the first pass dominates
disk I/O and serving threads starve. Steady-state runs touch
only the small delta since the last run and are fast.

**Recovery — phase 1 (parse loop).**

Patch the liveness probe in-cluster to survive cold start:

```sh
kubectl patch statefulset -n kube-system zot --type=json -p='[
  {"op":"replace",
   "path":"/spec/template/spec/containers/0/livenessProbe/failureThreshold",
   "value":60}
]'
```

This bumps `failureThreshold` from 3 to 60 (10 min). The
StatefulSet rolls a new pod; if Flux subsequently reconciles
the HelmRelease, **the patch is overwritten** — see prevention
below.

**Recovery — phase 2 (post-parse latency).**

Wait it out. Background ops settle in 30–60 min. CI can be
unblocked by **admin-bypass merging** PRs whose CI failures are
specifically `Extract images` / `Flux Local Test` chart-fetch
timeouts against ZOT — the failures are pure infrastructure
flake, not PR content.

**Prevention.** The in-cluster patch in
"Recovery phase 1" is a hotfix. The durable fix is in Git:

1. Add a `startupProbe` to the ZOT HelmRelease in
   `kubernetes/apps/kube-system/zot/app/helmrelease.yaml` so
   liveness/readiness don't apply until startup completes:

   ```yaml
   probes:
     startup:
       enabled: true
       custom: true
       spec:
         httpGet:
           path: /v2/
           port: 5000
         initialDelaySeconds: 30
         periodSeconds: 30
         failureThreshold: 60   # 30 min cold-start budget
   ```

2. (Optional) Configure ZOT's `extensions.scrub.delay`,
   `extensions.gc.delay`, and `retention.schedule` so the
   first post-restart pass doesn't fire immediately. See
   ZOT v2 config docs.

3. Open a `workaround:` issue per
   `.agents/instructions/workarounds.md` linking back to this
   runbook section so the in-cluster patch isn't quietly lost
   on the next HelmRelease reconcile.

**Cross-reference.** The user-visible symptom that *first*
exposes this on a busy cluster is "Django ADMINS email about
postgres connection errors" — the postgres-zulip primary
takes a brief connection flap during PG re-peering, Django's
ADMINS hook fires. The Postgres process itself doesn't
restart; reconnect is automatic; data is intact. See the
2026-05-21 incident notes for the exact timing.

## Stalled HelmRelease with `MissingRollbackTarget`

**Symptom.** A HelmRelease shows `Ready=False` with condition
`Stalled / MissingRollbackTarget`: "Failed to perform remediation:
missing target release for rollback: cannot remediate failed
release." `helm history -n <ns> <release>` shows **every** release
in `failed` state — there is no successful version to roll back
to. Underlying resources (Deployment, Service, etc.) may
nevertheless be live and serving traffic.

**How to confirm.**

```sh
kubectl get helmrelease -n <ns> <name> -o json \
  | jq '.status.conditions[] | select(.type=="Stalled")'
helm history <release> -n <ns>
```

If all release versions are `failed` and the underlying
Deployment is `Available`, the chart's resources have converged
despite Helm's history saying otherwise — Helm's wait/timeout
fired before the pod became `Ready`.

**Root cause.** The HelmRelease's effective install/upgrade
timeout (`spec.timeout`, default 5 m) is shorter than the time
the first Deployment took to become `Ready`. Common drivers:
Istio sidecar warmup, slow image pull on first install, slow
ExternalSecret resolution. Once the first install fails, Flux
attempts retries; each retry also times out, and after the
configured `retries` count the release is `Stalled` with no
healthy version to remediate to.

**Prevention.** Set `spec.timeout` on the HelmRelease to cover
the slowest-realistic warmup for the workload (15 m is a safe
default for Istio-injected pods). See
`kubernetes/apps/mcp-system/windmill-mcp/app/helmrelease.yaml`
for the canonical example.

**Recovery.** *Destructive — propose to operator before running.*
The Deployment and friends already exist; we need to make Helm's
storage agree.

1. Suspend Flux reconciliation so it doesn't fight the cleanup:

   ```sh
   flux suspend hr <name> -n <ns>
   ```

2. Verify the underlying Deployment is healthy:

   ```sh
   kubectl get deploy,svc,sa,httproute -n <ns> \
     -l app.kubernetes.io/instance=<name>
   ```

3. Delete the chart resources and the failed Helm history
   secrets together — Helm won't adopt resources it doesn't own,
   so a brief downtime is unavoidable:

   ```sh
   kubectl delete deployment,svc,sa,httproute \
     -n <ns> -l app.kubernetes.io/instance=<name>
   kubectl delete secret -n <ns> -l owner=helm,name=<name>
   ```

4. Resume Flux and force reconcile; the HR will do a clean
   `helm install`:

   ```sh
   flux resume hr <name> -n <ns>
   flux reconcile hr <name> -n <ns> --force
   ```

5. Verify the HR becomes `Ready=True` within the new
   `spec.timeout` window:

   ```sh
   kubectl get hr -n <ns> <name> -w
   ```

Time budget: 2–3 m of downtime per HR. Pick a maintenance
window per `CLAUDE.md` if the component is operator- or
household-facing.
