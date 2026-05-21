# Offsite Recovery — Immich and Paperless

Restoring Immich or Paperless from the offsite Glacier Deep Archive backups.
Both apps follow the same shape: documents/photos in `crypt:media` (or
`crypt:data` + `crypt:external` for Immich), CNPG/Barman database backups in
`crypt:db`. The crypt overlay encrypts everything before it lands in S3 — the
crypt passphrase from 1Password is the only thing that makes the backup
recoverable. **Lose both 1Password and the cluster, and the backup is opaque
ciphertext.**

The buckets:

| App | S3 bucket | 1Password item |
|---|---|---|
| Immich | `lovenet-immich-offsite-backup` | `immich-offsite-backup` |
| Paperless | `lovenet-paperless-offsite-backup` | `paperless-offsite-backup` |

## Prerequisites for any restore

- AWS credentials with read access to the bucket (the same keys stored in the
  1Password item, or any IAM principal that can `s3:GetObject` /
  `s3:RestoreObject`).
- The crypt passphrases (`RCLONE_CRYPT_PASSWORD` + `RCLONE_CRYPT_PASSWORD2`) from
  1Password.
- A working `rclone` somewhere — laptop, recovery box, or in-cluster pod.
- For database restore: a CNPG cluster on the destination (existing or fresh).

## Choosing a recovery scenario

| Scenario | What survived | What to do |
|---|---|---|
| A — Cluster destroyed, NFS + Longhorn intact | All PVCs and Garage backups | Rebuild cluster, reattach PVs, restart apps. **Skip offsite restore entirely.** |
| B — NFS/Longhorn destroyed, cluster intact | Postgres-in-Garage may also be destroyed if Garage was on the failed storage | Thaw S3, restore files to new PVCs, restore DB via Barman from S3 |
| C — Total loss (cluster + storage) | Only S3 | Bootstrap new cluster per `cluster_rebuild.md`, then follow Scenario B |

Most "disaster" cases are actually Scenario A in disguise. Don't reach for the
offsite copy unless the local data is genuinely gone.

## Scenario B and C — restoring from S3

### Step 1: Thaw the Glacier Deep Archive objects

Lifecycle moves objects to Deep Archive after 1 day. Anything older than that is
cold-stored and takes 12+ hours (Standard tier) or ~48 hours (Bulk) to thaw.

Bulk-restore everything in the bucket:

```sh
# Immich
aws s3api list-objects-v2 --bucket lovenet-immich-offsite-backup \
    --query 'Contents[?StorageClass==`DEEP_ARCHIVE`].Key' \
    --output text \
  | tr '\t' '\n' \
  | while read -r KEY; do
      aws s3api restore-object \
        --bucket lovenet-immich-offsite-backup \
        --key "$KEY" \
        --restore-request '{"Days":7,"GlacierJobParameters":{"Tier":"Standard"}}'
    done

# Paperless — same pattern, swap the bucket name
```

Tier choice: **Standard** for a real recovery (12h, ~$0.01/GB). **Bulk** if the
event isn't urgent (48h, ~$0.0025/GB). For a 2.5 TB Immich library Bulk saves
~$20.

Wait until `aws s3api head-object --bucket … --key … --query Restore` returns
`ongoing-request="false"`.

### Step 2: Stand up a temporary rclone with the right config

In-cluster pod is easiest because the existing offsite-backup secret already has
the rclone.conf. If the cluster is gone, recreate the rclone.conf locally from
the 1Password item — same format as in
`kubernetes/apps/{media,collab}/{immich,paperless}/app/externalsecret-offsite-backup.yaml`.

```sh
# Local recovery — reconstruct rclone.conf
cat > /tmp/rclone.conf <<'EOF'
[s3]
type = s3
provider = AWS
access_key_id = <AWS_ACCESS_KEY_ID from 1Password>
secret_access_key = <AWS_SECRET_ACCESS_KEY from 1Password>
region = us-east-1

[crypt]
type = crypt
remote = s3:lovenet-immich-offsite-backup
filename_encryption = standard
directory_name_encryption = true
password = <RCLONE_CRYPT_PASSWORD from 1Password>
password2 = <RCLONE_CRYPT_PASSWORD2 from 1Password>
EOF
chmod 0600 /tmp/rclone.conf
export RCLONE_CONFIG=/tmp/rclone.conf

# Sanity: list the encrypted root — should show "data", "external", "db" for Immich
rclone lsd crypt:
```

### Step 3: Restore the files

Immich (NFS-backed in normal operation; choose any RWX target on recovery):

```sh
rclone copy crypt:data    /restore/immich/data    --transfers=8 --progress
rclone copy crypt:external /restore/immich/external --transfers=8 --progress
```

Paperless (Longhorn-backed in normal operation; restore into a fresh
`paperless-library-pvc`):

```sh
rclone copy crypt:media /restore/paperless/library/media/documents --transfers=8 --progress
```

The thumbnails were excluded from backup. Paperless will regenerate them
automatically once the app starts and reindexes; nothing to do.

### Step 4: Restore the database via Barman

The `crypt:db` tree mirrors what CNPG/barman-cloud writes to Garage. Push it
back into a new Garage bucket (or any S3-compatible target) and point a new
CNPG cluster at it.

#### 4a. Sync the encrypted DB backup back to a recoverable S3 location

```sh
# In the recovered cluster, after Garage is back up:
rclone copy crypt:db garage-recovered:postgres-immich-backup
```

If the cluster is fresh, you can also use AWS S3 directly as the Barman source
during restore by adding a temporary `[s3-recovery]` remote in rclone.conf and
syncing decrypted contents to a regular bucket. Garage is preferred because
that's where the existing CNPG `ObjectStore` resources point.

#### 4b. Bootstrap a new CNPG cluster from the backup

Edit the CNPG `Cluster` manifest (e.g.
`kubernetes/apps/databases/cloudnative-pg/config/immich/cluster.yaml`) to add a
`bootstrap.recovery` section pointing at the Barman ObjectStore:

```yaml
spec:
  bootstrap:
    recovery:
      source: postgres-immich-backup
  externalClusters:
    - name: postgres-immich-backup
      plugin:
        name: barman-cloud.cloudnative-pg.io
        parameters:
          serverName: postgres-immich
          barmanObjectName: garage-immich
```

Or, for a clean new cluster name (recommended — avoids any race with a
half-existing cluster of the same name):

```yaml
metadata:
  name: postgres-immich-restored
spec:
  bootstrap:
    recovery:
      source: postgres-immich-backup
      # Optional: target a specific point in time
      # recoveryTarget:
      #   targetTime: "2026-05-04 18:00:00+00"
```

Apply, wait for the cluster to come up, verify with `kubectl cnpg status -n
databases postgres-immich-restored`. Then point the immich helmrelease at the
new cluster name and reconcile.

For paperless: same procedure, swapping `immich` for `paperless` everywhere.

### Step 5: Bring the app up against restored data

Immich:

1. Update `kubernetes/apps/media/immich/app/nfs-pvc.yaml` if the NFS path
   changed (the PV's `nfs.path` and `nfs.server`).
2. Make sure `immich-secret` still has valid `DB_*` env vars matching the new
   CNPG cluster.
3. Reconcile the immich kustomization. Immich will reindex thumbnails / ML
   embeddings on first run — expect significant CPU load for hours.

Paperless:

1. PVC bound to fresh Longhorn volume containing the restored
   `library/media/documents/`.
2. CNPG cluster restored, paperless-secret pointing at it.
3. Reconcile. Paperless regenerates thumbnails on first display; OCR /
   classifier indexes are restored from the DB.

### Step 6: Verify

- Immich: log in, browse a known album. Spot-check that EXIF dates, faces, and
  user edits survived (those are DB-backed).
- Paperless: search for a known document. Confirm tags, correspondents, custom
  fields, and OCR text are present.
- Both: trigger a fresh offsite-backup CronJob run manually
  (`kubectl create job --from=cronjob/<name> ...`) to confirm the next
  scheduled cycle will work cleanly.

## What to do *before* you need this

- Run the offsite backup at least once weekly per app — both CronJobs are
  scheduled `@weekly` already.
- Periodically (~quarterly) thaw a single small object via
  `aws s3api restore-object` and verify rclone+crypt can decode it. Both the
  Immich data path (2.5 TB, 5-file sample round-trip) and the Paperless data
  path (778 MiB across docs + DB) were validated 2026-05-05.
- Keep the 1Password vault backed up independently. If 1Password is also
  destroyed, the encrypted S3 contents are unrecoverable.

## Related

- `cluster_rebuild.md` — bootstrapping a fresh cluster from this repo
- `immich_cnpg.md` — Immich's *built-in* DB export format restore (different
  format than Barman; only applies when an export was taken from inside Immich)
