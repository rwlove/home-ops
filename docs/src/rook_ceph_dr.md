# Rook-Ceph DR Runbook

Procedural recovery for Rook-Ceph failures in this cluster. Paper
runbook — commands are documented but **not** rehearsed; verify
against `kubectl rook-ceph -- ...` before running any destructive
step.

## Cluster baseline

- Deployment: `kubernetes/apps/rook-ceph/rook-ceph/`
- ~8 OSDs spread across worker nodes, 3-way replication on the
  `ceph-block` pool (per `storage-class.instructions.md`)
- Mons live on dedicated control-plane / mon nodes per the
  CephCluster CR
- Toolbox pod: `rook-ceph-tools-*` in the `rook-ceph` namespace —
  `kubectl exec -it -n rook-ceph deploy/rook-ceph-tools -- ceph -s`

## Tier 1 — Single OSD loss

**Symptom:** `ceph -s` shows `HEALTH_WARN`, one OSD `down/out`,
recovery in progress.

**Diagnose:**

```sh
kubectl rook-ceph -n rook-ceph -- ceph osd tree
kubectl rook-ceph -n rook-ceph -- ceph -s
kubectl -n rook-ceph get pods -l app=rook-ceph-osd | grep -v Running
```

**Recover:**

1. If the OSD pod is `CrashLoopBackOff` and the underlying disk is
   intact, deleting the pod usually self-heals after Rook re-creates
   it. Wait 5 minutes before escalating.
2. If the disk is dead, mark the OSD `out`:

   ```sh
   kubectl rook-ceph -n rook-ceph -- ceph osd out <id>
   ```

   Wait for `ceph -s` to show `recovery_io` complete (can run hours
   at ~683 KB/s — see `docs/src/cluster_upgrade.md` note about
   global recovery events). Then purge:

   ```sh
   kubectl rook-ceph -n rook-ceph -- ceph osd purge <id> --yes-i-really-mean-it
   ```

3. Edit the CephCluster CR to remove the dead device, or replace
   the disk and let Rook re-provision via auto-discovery.
4. Verify replication health: `ceph -s` reports `HEALTH_OK` and
   zero `misplaced` or `degraded` PGs.

With 3-way replication and 8 OSDs, losing one OSD is fully
tolerated. The recovery window is the only at-risk period; don't
lose a second OSD during it.

## Tier 2 — Mon quorum loss

**Symptom:** `ceph -s` hangs or reports `mon election`; pods using
ceph-block PVCs get IO errors.

**Diagnose:**

```sh
kubectl -n rook-ceph get pods -l app=rook-ceph-mon
kubectl -n rook-ceph logs deploy/rook-ceph-operator | tail -100
```

**Recover:**

This cluster runs an odd-numbered mon set (3 mons). Quorum requires
2 of 3 alive.

1. **If 2/3 mons alive:** quorum holds. Identify the dead mon's
   node, fix the node (reboot, replace disk), and let Rook
   re-provision.
2. **If 1/3 mons alive:** quorum lost. Rook's `monMaxOSDChange`
   safeguard kicks in. Recovery path:
   - Identify the surviving mon (`ceph mon stat`).
   - Edit the `cephclusters.ceph.rook.io` CR to reduce mon count to
     1 temporarily.
   - Rook re-elects with the surviving mon as quorum-of-1.
   - Add mons back one at a time, verifying quorum at each step.
3. **If 0/3 mons alive:** disaster — see Tier 3.

The Rook-Ceph upstream toolbox has explicit mon-recovery scripts
(`rook-ceph-mon-quorum-recovery`). Use those before manual quorum
edits. Reference:
<https://rook.io/docs/rook/v1.18/Storage-Configuration/Advanced/ceph-mon-health/>

## Tier 3 — Full Ceph cluster loss

**Symptom:** all mons dead, OSDs dead, or the rook-ceph namespace
itself trashed.

**Recover:**

ceph-block is the in-cluster durable tier, **not** the
cluster-loss-survivable tier. Per `storage-class.instructions.md`,
data on ceph-block does NOT survive a Ceph cluster wipe unless it
was also being shipped offsite.

What survives:

- **CNPG cluster data** — yes, via Barman ObjectStore backups to
  Garage. Every CNPG `Cluster` in this repo has a paired
  `barmancloud.cnpg.io/ObjectStore`. Restore path: see
  `cnpg_restore.md`.
- **Longhorn-backed apps** — yes, via the backup target on
  beast NFS. Restore path: see `longhorn_restore.md`.
- **Garage substrate** — yes, via the NFS substrate (Garage's
  storage isn't ceph-block-backed). Garage stays intact.
- **Anything else on ceph-block** — no. Application configuration
  on ceph-block is regenerable (per the storage-class doc), so the
  app will reconstruct on next pod start.

**Steps:**

1. Confirm no path to recover — Tier 1 / Tier 2 procedures
   exhausted.
2. Suspend the `rook-ceph-cluster` Flux Kustomization to prevent
   automatic re-reconciliation while you assess.
3. Drain workloads using `ceph-block` PVCs:
   `kubectl get pvc -A -o yaml | yq '.items[] | select(
   .spec.storageClassName == "ceph-block") | .metadata.namespace +
   "/" + .metadata.name'` — scale them to zero.
4. Tear down the existing rook-ceph deployment per
   <https://rook.io/docs/rook/v1.18/Storage-Configuration/Advanced/ceph-teardown/>
   — `Cluster`, `OperatorConfig`, then delete the namespace.
5. Wipe the OSD disks on each worker (Rook-Ceph teardown does NOT
   wipe disks; you must `dd if=/dev/zero of=/dev/<osd-disk>
   bs=1M count=100` on each before re-deploying).
6. Re-deploy rook-ceph: unsuspend the Kustomization, let Flux
   reconcile a fresh CephCluster.
7. Wait for `HEALTH_OK` + zero PGs in unknown state.
8. Restore data per the per-tier runbooks (CNPG → cnpg_restore.md;
   Longhorn → longhorn_restore.md).
9. Scale the drained workloads back. Apps regenerate config on
   first start.

Expect Tier 3 recovery to take 4–8 hours total: ~30 min teardown,
~30 min fresh deploy, the rest is data restore + service smoke.

## Common gotchas

- **`Drain hangs ~indefinitely on rook-ceph-osd-host-*` PDB.** A
  previous node's OSD is still degraded; Rook's per-host PDB
  blocks all OSD evictions cluster-wide. See
  `docs/src/cluster_upgrade.md`. Two options: wait for `ceph -s`
  HEALTH_OK + zero remapped/misplaced PGs, OR `kubectl delete pod
  -n rook-ceph rook-ceph-osd-N-...` directly (bypasses eviction
  API; safe with 8 OSDs + 3-way replication for the ~10-min
  recovery window).
- **OSD pods stuck on `Init:0/N`.** Usually a `lvm tag`
  mismatch on the disk after a hard power loss. Run the
  rook-ceph-tools `ceph-volume lvm zap --osd-id <id>` to clear,
  then let Rook re-provision.
- **`HEALTH_WARN: mons are allowing insecure global_id reclaim`** —
  cosmetic; clear with `ceph config set mon auth_allow_insecure_global_id_reclaim false`.

## Verification checklist

After any Tier 1/2/3 recovery:

- [ ] `ceph -s` → `HEALTH_OK`
- [ ] `ceph osd tree` → all expected OSDs `up/in`
- [ ] `ceph -s` shows zero `degraded`, `misplaced`, `inactive` PGs
- [ ] A representative ceph-block PVC mounts cleanly
  (`kubectl run dr-verify --image=alpine -- sh -c "sleep 60"`
  with a PVC attached)
- [ ] No `CephClusterErrorState` PrometheusAlert firing

## See also

- `docs/src/cluster_upgrade.md` — node-drain interactions with OSD PDB
- `cnpg_restore.md` — restore CNPG data after ceph-block loss
- `longhorn_restore.md` — restore Longhorn-backed apps
- `garage_restore.md` — restore Garage (NFS-substrate, doesn't
  depend on ceph)
- Memory: `[[reference_predict_linear_sparse_data_false_positives]]` —
  PR #11755 disk-fill alerting context
