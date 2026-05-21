# Garage Substrate Restore Runbook

Recovery procedure for the Garage S3-compatible object store
deployed in this cluster. Paper runbook — verify commands against
the running deployment before executing destructive steps.

## Cluster baseline

- Deployment: `kubernetes/apps/storage/garage/`
- API endpoint: `s3.${SECRET_DOMAIN}`
- Substrate: NFS-backed PVCs on `brain`
  - `/mnt/kubernetes/garage/data/` — object data
  - `/mnt/kubernetes/garage/meta/` — Garage internal metadata
- Both directories are on `brain`'s `/mnt/mass_storage` RAID6
  (tolerates 2-disk loss)
- Storage tier (per `storage-class.instructions.md`):
  S3-compatible, used for CNPG Barman backups + app-level S3
  workloads + offsite-backup destinations.

## Buckets in use

- `cnpg-<app>` (one per CNPG cluster) — Barman base + WAL
- App-level workloads using S3 API (e.g. Loki object store if
  configured, Velero if deployed, etc.)
- Offsite-backup CronJobs (`immich-offsite-backup`,
  `paperless-offsite-backup`) — staging or direct write

## When to use this

- Garage pod won't start (metadata corruption)
- One or both substrate PVCs lost/corrupted
- Whole-cluster rebuild — Garage redeployed fresh, needs the NFS
  substrate re-mounted

## Recovery scenarios

### Scenario A — Garage pod crash, substrate intact

Pod is in CrashLoopBackOff but the NFS-backed PVCs are healthy.

1. Diagnose:

   ```sh
   kubectl -n storage logs sts/garage --previous
   kubectl -n storage describe pod garage-0
   ```

2. Common causes:
   - Metadata DB corruption — Garage writes to `meta/` and a hard
     crash can leave the LMDB-style metadata wedged. Logs show
     `lmdb error: MDB_CORRUPTED`.
   - Disk full — `meta/` or `data/` directory at 100%.
   - Network partition — Garage couldn't reach its peers (in a
     multi-replica setup; this cluster runs single-replica, so
     this is rare).

3. For metadata corruption: Garage has a `garage repair` subcommand:

   ```sh
   kubectl -n storage exec sts/garage -- garage repair --yes scrub
   ```

4. For disk-full: free space on the NFS mount on brain or expand
   the underlying filesystem.
5. Pod auto-recovers once root cause is fixed.

### Scenario B — NFS substrate PVCs lost

The brain NFS mounts are gone (filesystem wipe, RAID failure, etc.).
This is rare given the RAID6 substrate but possible.

**What this means in practice:**

- All buckets are gone — CNPG Barman backups for all clusters are
  lost. CNPG clusters revert to "only PGData survives" recovery.
- Offsite-backup staging is gone — but the offsite target (AWS
  Glacier for Immich + Paperless) is independent.
- Apps that rely on Garage for S3 storage have lost their data.

**Recovery:**

1. Rebuild the NFS substrate on brain — see brain's host-side
   recovery (not in this repo; see
   `lovenet-network-configuration/` for brain's role).
2. Recreate the NFS exports:

   ```sh
   ssh brain 'exportfs -ra && exportfs'
   # Verify /mnt/kubernetes/garage/{data,meta} are exported
   ```

3. The `nfs-pvc.yaml` resources in
   `kubernetes/apps/storage/garage/app/` reference these by NFS
   server + path. Once the exports return, the PVCs bind on next
   pod restart.
4. Garage starts with an empty `meta/` and `data/` — it's a fresh
   cluster from Garage's perspective.
5. Recreate buckets by reconciling Flux (the
   `objectbucketclaim.objectbucket.io/v1alpha1` resources in each
   app's manifests will re-request their buckets).
6. CNPG clusters: next `ScheduledBackup` populates the new
   `cnpg-<app>` buckets. **Existing data is not recovered** — only
   future backups. To recover historic data, you needed offsite.
7. Verify the `s3.${SECRET_DOMAIN}` route returns 200 from outside
   the cluster.

### Scenario C — whole-cluster rebuild (Garage redeployed fresh)

The cluster was destroyed and rebuilt. Garage HelmRelease
reconciled, but the NFS substrate on brain may or may not still
have data.

1. Check brain:

   ```sh
   ssh brain 'ls -la /mnt/kubernetes/garage/data/ /mnt/kubernetes/garage/meta/'
   ```

2. **If substrate is intact:** Garage sees the existing data + meta
   on first start, reconstitutes its bucket list automatically.
   All previous buckets and objects appear. No further action
   needed.

3. **If substrate is empty/missing:** treat as Scenario B.

The substrate is on `brain` and persists across cluster
destruction by design — that's the whole point of NFS-backed
Garage. So Scenario C is almost always "intact" in practice.

## Gotchas

- **Garage repair scrub takes hours** on a populated cluster.
  Schedule during low-IO window.
- **The Garage admin secret** is in 1Password
  (`Kubernetes/garage`); without it, you can't `garage` CLI into
  the running pod for repair. Verify access before you need it.
- **`s3.${SECRET_DOMAIN}` DNS** must resolve from the cluster
  internally — per memory
  `[[feedback_cilium_world_egress_split_horizon]]`, internal
  resolves go to the LB IP via bind9 split-horizon. CNPs that use
  `toEntities: world` for Garage egress silently drop. Use
  cluster-internal Service URLs (`garage.storage.svc.cluster.local`)
  in pod configs.
- **OBC (ObjectBucketClaim) hangs after a Garage rebuild** — the
  `objectbucket.io` controller cached the old bucket UUID. Delete
  the OBC + its backing secret, let Flux re-create.

## Verification checklist

After any restore:

- [ ] `kubectl -n storage get pods` shows `garage-0` Running
- [ ] `garage status` from inside the pod reports the expected
  node count + capacity
- [ ] `s3.${SECRET_DOMAIN}` route returns HTTP 200 (or expected
  S3 XML error for unauthed)
- [ ] A test `aws s3 ls --endpoint-url
  https://s3.${SECRET_DOMAIN}` succeeds with valid credentials
- [ ] CNPG clusters can write to their ObjectStores — `kubectl
  -n databases get backups` shows recent `Completed` entries

## See also

- `rook_ceph_dr.md` — independent storage tier
- `cnpg_restore.md` — Garage's primary consumer
- `offsite_recovery.md` — offsite is independent of Garage
- `storage-class.instructions.md` — when to use Garage vs other
  tiers
- Memory: `[[feedback_cilium_world_egress_split_horizon]]` — Cilium
  - split-horizon DNS gotcha when egressing to Garage
- Memory: `[[reference_glacier_verify_script_pitfalls]]` —
  adjacent gotcha for offsite verification
