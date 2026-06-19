# Storage class selection

This cluster has several persistent-storage backends, layered by
durability. Pick deliberately — "just use Longhorn" or "default to
ceph-block" is rarely the right answer for new apps. The framing
below is **durability-driven**: pick the tier that matches the
worst-case the data has to survive.

## Durability hierarchy

| Tier | Survives app restart | Survives node loss | Survives Ceph cluster loss | Survives k8s cluster loss | Survives total site loss |
|---|---|---|---|---|---|
| `ceph-block` (non-CNPG) | ✓ | ✓ | ✗ | ✗ | ✗ |
| `ceph-block` + Garage (CNPG) | ✓ | ✓ | ✗ (data) ✓ (backups) | ✓ (Barman restore) | ✓ (Garage→AWS offsite) |
| `longhorn` + recurring backup | ✓ | ✓ | ✓ | ✓ (NFS restore) | ✗ |
| Garage (S3) | ✓ | ✓ | ✓ | ✓ (NFS) | ✓ (offsite rclone) |
| direct NFS | ✓ | ✓ | ✓ | ✓ | ✗ (depends on app-level shipping) |

## Decision tree

1. **Database (CNPG `Cluster`)** → **`ceph-block`** for PGData,
   paired with a `barmancloud.cnpg.io/ObjectStore` writing to Garage
   for DR. No exceptions in this cluster — every CNPG cluster follows
   this pattern; deviations require measured justification.

2. **Application configuration and other regenerable data** — cache,
   downloaded models, derived state, anything reconstructable from
   git/upstream/an external source/another DB — including app config
   that *could* theoretically be re-fetched on restart → **`ceph-block`**.
   Pod restarts shouldn't trigger re-fetch; `ceph-block` is the
   in-cluster-durable default.

3. **Irreplaceable data** — accumulated state, keys, history,
   anything that **cannot** be rebuilt from elsewhere → **`longhorn`**
   with the weekly + monthly recurring-backup labels applied. The
   NFS-backed backup target (`nfs://beast:/mnt/mass_storage/longhorn-backups`)
   is what makes this the durable-through-cluster-destruction tier.

4. **S3-shaped workload** (object storage API, app-level offsite
   backup targets) → **Garage** (`s3.${SECRET_DOMAIN}`).

5. **Large media on a single node, or workload needing filesystem
   semantics not exposed by `ceph-block` / Longhorn** (xfs prjquota,
   specific mount options) → **direct NFS** via per-app PV + PVC
   pattern (see `media/jellyfin/app/nfs-pvc.yaml` for a canonical
   example).

6. **Genuine scratch** — tmp dirs, build artifacts, in-memory caches
   that legitimately rebuild on every pod lifecycle — **`emptyDir`**
   or **`tmpfs`** (`type: emptyDir` in bjw-s persistence). **Not** for
   application configuration even when it looks cache-like — see
   rule 2.

7. **`hostPath`** — escape hatch only. Performance-critical workloads
   where the local-disk binding is the point. **Never** for data that
   needs to survive a pod reschedule. Document the reason next to
   the volume.

## Per-backend notes

### Rook/Ceph (`ceph-block`)

The default durable-in-cluster tier. RWO, replicated across Ceph
OSDs. Survives node loss and app restarts. **Does not survive
Ceph-cluster loss or full-cluster rebuild** — for cluster-loss
durability use Longhorn, for offsite use Garage. Provisioned
automatically by the StorageClass; just request a PVC.

Bucket access (`ObjectBucketClaim` / `ceph-bucket`) is also available
but Garage is preferred for new S3 workloads.

### Longhorn

The cluster-destruction-survivable tier. Used for **irreplaceable**
state. The chart's `backupTarget` points at
`nfs://beast:/mnt/mass_storage/longhorn-backups`, and
`recurring-jobs.yaml` defines `weekly-backups` + `monthly-backups`
(plus `daily-snapshots`, `weekly-filesystem-trim`). The repo
convention is to declare a named StorageClass and matching PV per
app — see `media/jellyfin/app/longhorn-pvc.yaml` for the pattern.

New Longhorn volumes need:

- Labeled for the recurring backup jobs to pick them up. **Important
  mechanics:** Longhorn's recurring jobs select on the *Volume CR's*
  `recurring-job-group.longhorn.io/<group>` labels, **not** the PV's.
  PV labels do not propagate to the Volume CR — Longhorn instead
  **auto-applies the `default` group** to any volume whose CR has no
  recurring-job labels, and `default` is what actually protects nearly
  every volume in this cluster (daily snapshot + weekly + monthly
  backup + weekly trim). The named groups (`daily-snapshot`,
  `weekly-backup`, `weekly-snapshot`, `monthly-backup`) are wired into
  `longhorn/config/recurring-jobs.yaml` as functional aliases, so a
  volume whose CR *does* carry them is still covered. **Trap to avoid:**
  if a manual op (e.g. restoring a volume by re-creating the Volume CR)
  copies the PV's named labels onto the Volume CR, the volume drops out
  of auto-`default`; ensure a functional group label is present or it
  is silently un-backed-up. This bit `paperless-data-xfs` after its
  2026-06-19 restore — see
  [[reference_longhorn_snapshot_not_crash_consistent_use_backup]].
- A **detached** volume cannot be backed up (no engine to read from).
  CronJob-only or scaled-to-zero workloads (e.g. `beets`) whose volume
  is detached during the Saturday 00:00 UTC backup window silently miss
  weekly backups — their DR floor is whenever the volume last happened
  to be attached at backup time.
- `unmapMarkSnapChainRemoved=enabled` set per-Volume to prevent
  snapshot-pinned slack.
- `chmod 755` on `lost+found` if the app runs non-root — fresh ext4
  PVCs ship with `lost+found` at `root:root` mode-700, and non-root
  containers enumerating the root directory crash with `EACCES`.

Set `numberOfReplicas` based on the data's importance; media that
can be re-acquired runs at `1`, anything irreplaceable at `2+`.

Don't use Longhorn for generic config volumes — that's
`ceph-block`'s job (rule 2). Longhorn is specifically for data that
cannot be rebuilt from elsewhere.

### Garage (S3-compatible)

Lives at `kubernetes/apps/storage/garage/`, exposed at
`s3.${SECRET_DOMAIN}`. Use for:

- CNPG `barmancloud.cnpg.io/ObjectStore` (DB backups).
- App-level S3 workloads (anything natively speaking S3).
- Offsite-backup targets (immich/paperless rclone CronJobs).

Garage's substrate is the `garage-data` / `garage-meta` PVCs
(NFS-backed). **Do not put new workloads directly on those
StorageClasses** — if you're tempted to, you almost certainly want
Garage's S3 API instead.

### Direct NFS

Per-app PV + PVC pattern; pin `volumeName` and `nfs.server` /
`nfs.path` explicitly. Use for large media or workloads needing
specific filesystem semantics. NFS servers in use:

- **`beast`** (`NFS_HOST_2`) — primary bulk storage: media libraries
  (MP3s, pictures, Movies), app data, **Longhorn backup target**
  (`nfs://beast:/mnt/mass_storage/longhorn-backups` — see Longhorn
  HelmRelease's `backupTarget`). `/mnt/mass_storage` is **RAID6**
  (tolerates 2-disk failure).
- **`brain`** (`NFS_HOST_0`) — secondary storage: downloads
  (`/mnt/downloads`, `/mnt/downloads-nvme`), Television media,
  **Garage substrate** (`/mnt/kubernetes/garage/{data,meta}` — see
  `kubernetes/apps/storage/garage/app/nfs-pvc.yaml`).
  `/mnt/mass_storage` is **RAID6** (md1, 6 disks, tolerates 2-disk
  failure). Brain is also the home router/gateway.
- **`security-storage`** — Frigate camera data only (clips +
  recordings, with XFS project quotas).

Both beast and brain `/mnt/mass_storage` are RAID6 — durable enough
for any tier. Server choice is workload-driven: route media (movies,
pictures, MP3s, app data) to beast; downloads and TV episodes to
brain.

Server choice is workload-driven — match the existing path layout
rather than introducing a new mount on a new server unprompted.
Backup is app-specific (immich/paperless rclone CronJobs, snapshot
retention), not provided by the tier itself.

### NFS mountOptions by workload class

When declaring a direct-NFS PV, set `mountOptions` to its workload tier's full
set in a **single** block — `mountOptions` are immutable, so each later change
means deleting/recreating the PV and remounting the pod (a maintenance window;
Renee-facing PVs → Tuesday 02:00–04:00). For the reference pattern
(`["nfsvers=4.2","nconnect=8","hard","noatime"]`) see the already-tuned PVs at
`kubernetes/apps/media/immich/app/nfs-pvc.yaml` and
`kubernetes/apps/storage/garage/app/nfs-pvc.yaml`.

| Tier | Workloads | mountOptions |
|---|---|---|
| **A — static read libraries** | media-library read mounts (movies / music / pictures) | `nfsvers=4.2`, `nconnect=8`, `hard`, `noatime`, `actimeo=600` (aggressive attr cache — files rarely change) |
| **B — active scratch / pull** | media-pull-stack scratch + completed-download mounts | `nfsvers=4.2`, `nconnect=8`, `hard`, `noatime` — **no** aggressive attr cache (apps must see file changes promptly) |
| **C — already tuned (reference)** | the photo-management app + Garage S3 substrate | `nfsvers=4.2`, `nconnect=8`, `hard`, `noatime` |

- `nconnect=8` (parallel TCP) is the biggest single-client throughput lever; the
  cluster nodes support it (kernel ≥5.3). The **Kodi boxes cannot** (kernel 4.9)
  — their levers are server-side nfsd threads + client `buffermode`.
- **Sequencing:** beast-served mounts (the media libraries + image-gen output)
  gate behind beast's nfsd `8→16` bump (don't pile parallel connections on an
  8-thread pool); brain-served mounts (download/scratch + Garage + TV) can take
  `nconnect` anytime (brain is idle, already 16 threads, 26 GB cache).
- Keep server exports `sync` (durability) — `nconnect` + threads get the speed
  without the crash-consistency risk of `async`.

## What this is NOT

- A cost-optimization guide. We don't bin-pack across backends; pick
  the right tier for the data's durability needs.
- A guarantee that every existing PVC follows these rules — some
  predate the convention. Don't migrate them on a hunch; do an
  audit and capture a migration TODO before touching anything that's
  serving traffic.
