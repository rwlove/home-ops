# Storage class selection

This cluster has three persistent-storage backends. Pick deliberately —
"just use Longhorn" is rarely the right answer for new apps.

## Decision tree

1. **Is it an S3 / object-storage workload?** → **Garage**
   (`kubernetes/apps/storage/garage/`). Backups, image stores, anything
   speaking the S3 API. Examples: CNPG Barman backups, immich-machine-learning
   model cache.

2. **Is it a database (CNPG, Dragonfly, etc.)?** → **`ceph-block`**.
   Every CNPG `Cluster` in this repo uses `storageClass: ceph-block` for
   PGData, and barman backups go to Garage via `barmancloud.cnpg.io`.

3. **Is it a small RWO config volume (<10 Gi) for a stateless-ish app?**
   → **`ceph-block`**. Default for new apps; keep it boring.

4. **Is it large media (movies, recordings, libraries) on a single node,
   or do you need a specific filesystem layout (xfs, ext4, RWX) tuned
   per-volume?** → **Longhorn**, with a per-app named StorageClass
   (`<app>-<purpose>-storage-class`) and an explicit PV. See
   `kubernetes/apps/media/jellyfin/app/longhorn-pvc.yaml` for the
   canonical pattern.

5. **Anything else?** → Start with `ceph-block`. Switch only if you hit
   a concrete limit.

## Per-backend notes

### Rook/Ceph (`ceph-block`)
- The default. RWO. Replicated across Ceph OSDs.
- Use for: databases, application config, small volumes.
- Provisioned automatically by the StorageClass; just request a PVC.
- Bucket access (`ObjectBucketClaim` / `ceph-bucket`) is also available
  but Garage is preferred for new S3 workloads.

### Longhorn
- Used for large per-node volumes that benefit from filesystem-level
  tuning, or when you want explicit PV control (volumeName, fsType).
- The repo convention is to declare a named StorageClass and matching
  PV per app — see `media/jellyfin/app/longhorn-pvc.yaml`.
- Set `numberOfReplicas` based on the data's importance; media that
  can be re-acquired runs at `1`, anything irreplaceable at `2+`.
- Don't use for new generic config volumes; use `ceph-block`.

### Garage (S3-compatible)
- Lives at `kubernetes/apps/storage/garage/`, exposed at
  `s3.${SECRET_DOMAIN}`.
- Use for: CNPG `barmancloud.cnpg.io/ObjectStore`, app-level S3
  workloads, anything that natively speaks S3.
- Garage itself is backed by NFS PVCs (`garage-data`, `garage-meta`) —
  if you're tempted to put a new workload directly on those classes,
  you almost certainly want Garage's S3 API instead.

## What this is NOT

- A cost-optimization guide. We don't bin-pack across backends; pick
  the right one for the workload.
- A guarantee that every existing PVC follows these rules — some
  pre-date the convention. Don't migrate them on a hunch.
