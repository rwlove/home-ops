# Storage Operations

Day-2 health checks and troubleshooting for the cluster's three
in-cluster storage backends — Rook/Ceph, Longhorn, and Garage — plus
generic PVC troubleshooting. This is the **operate a healthy cluster**
page. It is not a disaster-recovery runbook: for restore procedures see
[`rook_ceph_dr.md`](rook_ceph_dr.md), [`longhorn_restore.md`](longhorn_restore.md),
and [`garage_restore.md`](garage_restore.md). For CNPG/Postgres storage
concerns (PGData sizing, Barman ObjectStore recency) see
`cnpg_operations.md`. For choosing a storage class for a new
workload, see `.agents/instructions/storage-class.instructions.md` in
the repo — that decision tree isn't repeated here.

## Rook/Ceph (`ceph-block`)

Deployment: `kubernetes/apps/rook-ceph/rook-ceph/`. Three StorageClasses
come out of the `rook-ceph-cluster` HelmRelease: `ceph-block` (RBD,
3-way replicated, the cluster default), `ceph-filesystem` (CephFS), and
`ceph-bucket` (RGW-backed object storage). `ceph-block` is what almost
every app-level PVC in this cluster uses.

### No toolbox pod — use a mon pod

This cluster does **not** run the `rook-ceph-tools` deployment (the
operator HelmRelease has no `toolbox.enabled: true`). All `ceph` CLI
work goes through a mon pod with explicit connection flags:

```bash
MON=$(kubectl get pod -n rook-ceph -l app=rook-ceph-mon -o name | head -1)
kubectl exec -n rook-ceph "$MON" -c mon -- bash -c "
  ceph --conf /etc/ceph/ceph.conf --keyring /etc/ceph/keyring-store/keyring -s
"
```

If the `kubectl rook-ceph` krew plugin is installed locally, it wraps
the same mechanics more conveniently:

```bash
kubectl rook-ceph -n rook-ceph -- ceph -s
kubectl rook-ceph -n rook-ceph -- ceph osd tree
```

Adding the toolbox is a tracked quality-of-life follow-up (see
[`failure_mode_runbooks.md`](failure_mode_runbooks.md)); until it lands,
budget the extra typing above for anything beyond a one-off check.

### Health checks

```bash
# Overall cluster health
kubectl rook-ceph -n rook-ceph -- ceph -s
kubectl rook-ceph -n rook-ceph -- ceph health detail

# OSD tree — up/down/in/out per OSD, per-host layout
kubectl rook-ceph -n rook-ceph -- ceph osd tree
kubectl rook-ceph -n rook-ceph -- ceph osd status

# Mon quorum
kubectl rook-ceph -n rook-ceph -- ceph mon stat
kubectl rook-ceph -n rook-ceph -- ceph quorum_status

# Pool capacity / usage
kubectl rook-ceph -n rook-ceph -- ceph df
kubectl rook-ceph -n rook-ceph -- ceph osd pool stats

# OSD pods + which node each lives on
kubectl -n rook-ceph get pods -l app=rook-ceph-osd -o wide

# PG state (recovery/backfill in progress, degraded/misplaced counts)
kubectl rook-ceph -n rook-ceph -- ceph pg stat
```

The cluster runs 8 OSDs across `master1` + `worker2`-`worker8` (one
device per node) with `ceph-block` at `replicated size: 3` and
`ceph-filesystem` the same. `ceph-bucket` (RGW) is erasure-coded
(2 data + 1 coding chunk). Losing a single OSD is fully tolerated;
don't lose a second one during the recovery window.

Read-only Prometheus alternative (no exec needed):

```bash
# Ceph health status as a Prometheus alert
kubectl -n observability get prometheusrules prometheus-ceph-rules -o yaml
```

Ceph dashboard (visual OSD tree, pool graphs, PG state) is reachable at
`rook.${SECRET_DOMAIN}`; password via
[`tools/get-ceph-password.sh`](https://github.com/rwlove/home-ops/blob/main/tools/get-ceph-password.sh).

### Capacity

`ceph df` is the source of truth for pool-level usage. Before approving
any change that grows `ceph-block` footprint (new large PVC, volume
expansion), confirm free capacity in the target pool is comfortably
above the request — this is an 8-OSD cluster with no automatic
node-add path, so headroom doesn't self-correct.

### If Ceph looks unhealthy

Don't improvise past a `HEALTH_WARN`/`HEALTH_ERR` — check
[`failure_mode_runbooks.md`](failure_mode_runbooks.md) first for the
"Stuck CSI RBD unmap (Ceph PG ghost-stuck)" runbook, which covers the
specific ghost-PG failure mode this cluster has hit more than once
(mount stuck `ContainerCreating`, `rbd unmap` returns `EBUSY` with no
holders). For OSD loss, mon quorum loss, or full-cluster loss, that's
DR territory — go to [`rook_ceph_dr.md`](rook_ceph_dr.md).

## Longhorn

Deployment: `kubernetes/apps/longhorn-system/longhorn/`. This is the
cluster-destruction-survivable tier, reserved for irreplaceable data
(per `storage-class.instructions.md`) — media-library metadata, HA's
local store, similar. Backup target:
`nfs://beast:/mnt/mass_storage/longhorn-backups`.

### Node and replica health

```bash
# Per-node Longhorn node state (scheduling, disk pressure)
kubectl get nodes.longhorn.io -n longhorn-system

# Per-node replica state — load-bearing during drains
kubectl get replicas.longhorn.io -n longhorn-system -o wide

# Volume detail (state, robustness, engine image)
kubectl -n longhorn-system get volumes.longhorn.io
kubectl -n longhorn-system get volumes.longhorn.io <volume-name> -o yaml
```

A healthy volume reports `state: attached` and `robustness: healthy`.
`robustness: degraded` means a replica is missing or rebuilding — not
an outage by itself, but a SPOF until it clears. `faulted` needs
investigation before it becomes data loss.

```bash
# Backup target reachability
kubectl -n longhorn-system get backuptarget default
# Should show Available=True
```

If `Available` is `False`, the NFS export on beast is unreachable —
check from a worker node:

```bash
ssh beast 'ls /mnt/mass_storage/longhorn-backups/'
```

### Degraded-volume triage

1. Identify the affected volume:
   `kubectl -n longhorn-system get volumes.longhorn.io | grep -v healthy`.
2. Check replica placement —
   `kubectl get replicas.longhorn.io -n longhorn-system -l longhornvolume=<volume>`.
   A replica stuck `Failed` or `Error` on a specific node points at
   node-level disk pressure or a dead device.
3. Longhorn normally self-heals a degraded volume by rebuilding a
   replacement replica automatically (`replicaAutoBalance:
   best-effort` is set cluster-wide). Give it time before intervening.
4. If a node is unreachable, use the eviction pattern to drain its
   replicas onto healthy nodes:

   ```bash
   kubectl patch -n longhorn-system nodes.longhorn.io <node> \
     --type=merge -p '{"spec":{"allowScheduling":false,"evictionRequested":true}}'
   # wait for the node's replica count to reach 0, then:
   kubectl patch -n longhorn-system nodes.longhorn.io <node> \
     --type=merge -p '{"spec":{"allowScheduling":true,"evictionRequested":false}}'
   ```

If `longhorn-manager-X` crash-loops with `bind: address already in
use`, an orphaned daemon process survived a bad shutdown on the host —
see [`debugging.md`](debugging.md) → Longhorn.

### Backup-recency verification — how the labeling actually works

Recurring jobs live at
[`kubernetes/apps/longhorn-system/longhorn/config/recurring-jobs.yaml`](https://github.com/rwlove/home-ops/blob/main/kubernetes/apps/longhorn-system/longhorn/config/recurring-jobs.yaml):

| Job | Schedule | Task | Groups | Retain |
|---|---|---|---|---|
| `daily-snapshots` | `0 0 * * *` (daily, 00:00 UTC) | snapshot | `default`, `daily-snapshot`, `weekly-snapshot` | 7 |
| `weekly-backups` | `0 0 * * 6` (Saturdays, 00:00 UTC) | backup | `default`, `weekly-backup` | 4 |
| `monthly-backups` | `0 0 1 * *` (1st of month, 00:00 UTC) | backup | `default`, `monthly-backup` | 6 |
| `weekly-filesystem-trim` | `0 3 * * 0` (Sundays, 03:00 UTC) | filesystem-trim | `default` | 0 |

**Recurring-job labels live on the Volume CR, not the PV.** Longhorn's
job selection matches `recurring-job-group.longhorn.io/<group>` labels
on the *Volume* custom resource. A label on the PV does nothing —
labels don't propagate PV → Volume CR.

**`default` is what actually protects almost everything.** Longhorn
auto-applies the `default` recurring-job group to any Volume CR that
carries no explicit recurring-job labels at all, and every job above
lists `default` in its `groups`. In practice this means most volumes
in this cluster get daily snapshots + weekly backups + monthly
backups + weekly trim without any per-volume labeling. The named groups
(`daily-snapshot`, `weekly-snapshot`, `weekly-backup`, `monthly-backup`)
exist so that a volume whose CR *does* carry one of those labels
(typically after a manual restore that copied PV labels onto the new
Volume CR) still gets picked up by a real job instead of silently
falling out of `default` — check for exactly this after any manual
Volume CR recreation.

**Detached volumes are still covered.** `allowRecurringJobWhileVolumeDetached:
true` is set in the Longhorn HelmRelease `defaultSettings`. A detached
or scale-to-zero volume gets auto-attached, the recurring job runs,
then it detaches again. Don't design around the old "detached volumes
never get backed up" limitation — that gap was closed cluster-wide.
One side effect: if a workload starts while its volume is mid-job, it
waits for the auto-attach/detach cycle to finish — only relevant to a
volume that was already detached when the job fired.

To check a specific volume's actual last-backup timestamp:

```bash
kubectl -n longhorn-system get volumes.longhorn.io <volume-name> \
  -o jsonpath='{.status.lastBackupAt}{"\n"}'
```

Or via Prometheus — `LonghornVolumeBackupStale` (in
`kubernetes/apps/longhorn-system/longhorn/app/prometheus-rule.yaml`)
fires when an actively-used volume (PVC exists, a pod referenced it in
the last 7 days) has gone >14 days without a completed backup:

```bash
kubectl -n observability get prometheusrules longhorn-backup-rules -o yaml
```

An orphaned or parked volume (no PVC, or no pod using its PVC recently)
is deliberately excluded from that alert — a frozen volume's last
backup stays a valid recovery point no matter how old it gets.

### Capacity

```bash
kubectl get nodes.longhorn.io -n longhorn-system -o yaml | \
  yq '.items[] | .metadata.name + ": " + (.status.diskStatus | to_entries[] | .value.storageAvailable | tostring)'
```

Or use the Longhorn UI (`longhorn.${SECRET_DOMAIN}`) → Node page, which
renders per-node/per-disk usage directly.
`storageMinimalAvailablePercentage: 10` is the cluster-wide floor —
Longhorn stops scheduling new replicas to a disk below that threshold.

## Garage (S3)

Deployment: `kubernetes/apps/storage/garage/`, namespace `storage`,
StatefulSet `garage`. Exposed at `s3.${SECRET_DOMAIN}` externally and
`garage.storage.svc.cluster.local:3900` (S3 API) /
`:3903` (admin API) in-cluster. Substrate is two NFS-backed PVCs on
brain (`/mnt/kubernetes/garage/data`, `/mnt/kubernetes/garage/meta`),
provisioned as static PV/PVC pairs via
`kubernetes/apps/storage/garage/app/nfs-pvc.yaml` — not a StorageClass
provisioner.

This cluster runs Garage as a **single-node deployment**
(`replication_factor = 1` in `resources/configuration.toml`) — layout
health checks below are simple accordingly; there's no multi-node
quorum to reason about.

### Health and layout checks

```bash
# Pod health
kubectl -n storage get pods -l app.kubernetes.io/name=garage

# Garage's own status — node health, layout, capacity as Garage sees it
kubectl -n storage exec sts/garage -- garage status

# Bucket listing
kubectl -n storage exec sts/garage -- garage bucket list

# Per-bucket size/object count
kubectl -n storage exec sts/garage -- garage bucket info <bucket-name>

# Cluster layout (node roles, assigned capacity)
kubectl -n storage exec sts/garage -- garage layout show
```

`garage repair --yes scrub` is available for metadata corruption
symptoms (`lmdb error: MDB_CORRUPTED` in logs) — that's DR territory,
covered in [`garage_restore.md`](garage_restore.md).

### Capacity — two different numbers, don't confuse them

**Garage's own capacity setting** (what `garage layout show` reports,
configured per-node in Garage's layout) is separate from **the
underlying NFS filesystem capacity** on brain
(`/mnt/mass_storage`, RAID6). Garage can report headroom against its
configured layout capacity while the underlying brain filesystem is
actually tight, or vice versa. Check both before approving growth:

```bash
# Garage's view
kubectl -n storage exec sts/garage -- garage layout show

# The actual filesystem underneath
ssh brain 'df -h /mnt/mass_storage'
```

### Known consumers

Per the CiliumNetworkPolicy in
`kubernetes/apps/storage/garage/app/helmrelease.yaml`, in-cluster S3
clients reaching Garage directly (no envoy hop) are namespace-scoped:
`databases` (CNPG Barman ObjectStores — 20+ clusters), `media`
(offsite-backup rclone CronJob), and `collab` (offsite-backup rclone
CronJob). External S3 traffic comes in via the internal Envoy gateway
at `s3.${SECRET_DOMAIN}`.

## PVC troubleshooting

### Pending PVC

```bash
kubectl -n <ns> describe pvc <pvc-name>
```

Check the events at the bottom. Common causes:

- **No matching StorageClass** — `storageClassName` in the PVC spec
  doesn't exist. `kubectl get storageclass` to confirm the real name
  (`ceph-block`, `ceph-filesystem`, `ceph-bucket`, or an app-specific
  Longhorn/NFS class declared per-app).
- **Ceph pool capacity exhausted** — the CSI provisioner will report
  this in events; cross-check with `ceph df` above.
- **Provisioner pod stuck** — check the relevant CSI provisioner
  Deployment is `Running`
  (`kubectl -n rook-ceph get pods -l app=csi-rbdplugin-provisioner`
  for `ceph-block`; `kubectl -n longhorn-system get pods -l
  app=longhorn-manager` for Longhorn).
- **Static PV/PVC name/namespace mismatch** — for NFS or other
  statically-provisioned PVs (Garage's substrate, per-app Longhorn
  PVs), the PVC's `volumeName` must exactly match an existing,
  unbound PV. `kubectl get pv <name> -o yaml` to check
  `spec.claimRef`.

### Pod stuck mounting a PVC

```bash
kubectl -n <ns> describe pod <pod> | grep -A5 -E 'FailedMount|FailedAttachVolume'
```

- **Multi-attach error** (`Volume is already exclusively attached to
  one node`) — a RWO PVC's previous pod didn't detach cleanly. Confirm
  the old pod is actually gone (`kubectl get pods -A -o wide | grep
  <pvc-name>`), then let the CSI driver's attach/detach cycle finish;
  forcing it early risks a dirty volume.
- **Node affinity mismatch** — for a statically-bound PV with
  `nodeAffinity` (some NFS PVs pin a node), a pod scheduled elsewhere
  can't mount it. Check `spec.nodeAffinity` on the PV against the
  pod's actual node.
- **Ceph RBD ghost-stuck PG** — if the mount hangs specifically with
  `rpc error: code = Aborted ... an operation with the given Volume ID
  ... already exists`, that's the known failure mode in
  [`failure_mode_runbooks.md`](failure_mode_runbooks.md) → "Stuck CSI
  RBD unmap." Don't improvise past that; the runbook has the
  confirm-then-recover sequence.

### Changing an immutable PVC field

`storageClassName`, `accessModes`, and `resources.requests.storage`
(downward) are immutable once a PVC is bound. Editing them in place —
even via a Flux-managed manifest change — doesn't recreate the PVC;
Flux does a server-side **patch** on the same-named object, which
Kubernetes rejects, and the failure **blocks the entire Kustomization**
containing that PVC (every other object in it stops applying too,
`ReconciliationFailed`, `last_applied_revision` pinned to the old
commit).

The fix is a **new PVC with a distinct name**, not an in-place edit:

1. Add the new PVC (new storage class / access mode / size) under a
   **different `metadata.name`**.
2. Repoint the consumer's `persistence.<x>.existingClaim` (or
   `volumeName`, for static PVs) to the new name.
3. Remove the old PVC/PV from Git — Flux prunes it once nothing
   references it.
4. If the old PV's reclaim policy is `Retain`, its backing data is
   orphaned, not deleted — reclaim manually once you've confirmed the
   new claim is serving correctly.

Growing a PVC's size **upward** on the same storage class doesn't hit
this — `resources.requests.storage` can increase in place (`ceph-block`
and `ceph-filesystem` both have `allowVolumeExpansion: true`); only
storage-class/access-mode changes, or a size *decrease*, need the
distinct-name migration above.

## Capacity checks before approving growth

Before approving any PVC creation or volume-size increase, confirm
free capacity in the target backend is comfortably above the request:

| Backend | Check |
|---|---|
| `ceph-block` / `ceph-filesystem` / `ceph-bucket` | `ceph df` via a mon pod (see above) |
| Longhorn | Node page in the UI, or the `diskStatus` query above |
| Garage | `garage layout show` **and** `ssh brain 'df -h /mnt/mass_storage'` — both numbers, not just one |
| Direct NFS (beast/brain) | `ssh <host> 'df -h /mnt/mass_storage'` |

## See also

- [`rook_ceph_dr.md`](rook_ceph_dr.md), [`longhorn_restore.md`](longhorn_restore.md),
  [`garage_restore.md`](garage_restore.md) — restore procedures for
  each backend
- [`failure_mode_runbooks.md`](failure_mode_runbooks.md) — named,
  recurring failure modes including the stuck-CSI-RBD-unmap runbook
- [`debugging.md`](debugging.md) — general cluster-wide triage
  (events, logs, Flux state) this page doesn't repeat
- `.agents/instructions/storage-class.instructions.md` — which
  storage class to pick for a new workload
