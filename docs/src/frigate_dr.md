# Frigate + direct-NFS DR Runbook

Recovery procedure for Frigate (cameras, models, retention) and
adjacent direct-NFS workloads (jellyfin, immich originals,
unprocessed media). Paper runbook — verify before executing.

## Frigate baseline

- Deployment: `kubernetes/apps/home/frigate/`
- 7+ cameras integrated; XFS project quotas on the recordings
  volume for per-camera retention enforcement
- Camera storage: dedicated NFS export
  (`security-storage`-hosted), XFS with `prjquota` mount flag
- Frigate+ model: custom-trained, retraining cadence iterative
- Snapshot config (`frigate-snapshot` CronJob in `home/frigate/`)
  syncs `/config/config.yml` to git as documentation; the live
  config is in the PVC
- Memory references:
  - `[[project_frigate_snapshot_cronjob_broken]]` — alpine apk-add
    fails under egress restrictions
  - `[[reference_frigate_zone_semantics]]` — zone overlap +
    snapshot config direction

## What survives losses

| Loss scenario | What's gone | What survives |
|---|---|---|
| Frigate pod restart | nothing | clips/recordings on NFS, config in PVC |
| Frigate PVC loss | `/config/config.yml` (live edits), Frigate's local sqlite | clips/recordings on NFS, model on PVC if separate, config in git via snapshot CronJob (best-effort) |
| Frigate NFS export loss | all clips + recordings | the model file (if separate), config |
| Model file loss | model | config + clips on NFS |
| Whole-cluster wipe | Frigate sqlite (event index), live config | clips + recordings on NFS, model + config from git snapshot |

## Recovery scenarios

### Scenario A — Frigate pod won't start

Common after a Frigate version bump or a model file mismatch.

1. Diagnose:

   ```sh
   kubectl -n home logs frigate-0 -c app --previous
   kubectl -n home describe pod frigate-0
   ```

2. Common causes:
   - Model path in `config.yml` points at a missing file
   - GPU device-plugin not ready (Coral / VRAM allocation)
   - NFS mount unavailable
   - Config-file schema change between Frigate versions

3. Rollback the image to the prior known-good version (edit the
   HelmRelease, push, Flux reconciles). Frigate respects an
   in-place rollback if the on-disk database schema is compatible.

### Scenario B — Frigate config corruption

`/config/config.yml` in the PVC is wrong; the snapshot CronJob's
last git commit is the recovery source.

1. Verify the snapshot CronJob is current — check the last commit
   to `kubernetes/apps/home/frigate/snapshots/config.yml` (git
   log). The CronJob ran daily until it broke; recent state may
   be stale per memory note.
2. Re-apply the config:

   ```sh
   # From a host with kubectl access
   kubectl -n home cp snapshots/config.yml frigate-0:/config/config.yml -c app
   # Restart Frigate
   kubectl -n home delete pod frigate-0
   ```

3. Frigate restarts with the restored config.

If the snapshot CronJob has been broken (see project memory),
hand-recovery from the prior pod's logs or the user's memory is
the fallback.

### Scenario C — Camera NFS export lost

All clips + recordings are gone. The cameras themselves are
unaffected; new recordings start as soon as Frigate reconnects.

1. Rebuild the NFS export on `security-storage`:

   ```sh
   ssh security-storage 'ls /mnt/<frigate-export>'
   # Recreate the XFS filesystem if needed:
   # mkfs.xfs -f -m crc=1,bigtime=1 /dev/<device>
   # Mount with prjquota:
   # mount -o prjquota /dev/<device> /mnt/<frigate-export>
   ```

2. Re-apply XFS project quotas per camera (Frigate uses these for
   per-camera retention). The quota config is in Frigate's
   `config.yml` under each camera's `record.retain.days`; the
   actual filesystem-level project IDs are bootstrapped on first
   write by Frigate.
3. Restart Frigate: `kubectl -n home delete pod frigate-0`.
4. Verify each camera reconnects and starts writing.
5. **Historic clips are lost.** Frigate's event database
   (sqlite in the PVC) still references them; the references
   become 404s. Acceptable — Frigate self-prunes orphaned events
   on next compaction.

### Scenario D — Frigate+ model lost

The custom Frigate+ model in the model file is gone (PVC wipe,
file deletion).

1. Re-pull the model from Frigate+ — log into the Frigate+ portal,
   download the latest model.
2. Place it in Frigate's model path (per `config.yml`).
3. Restart Frigate.
4. Validation: Frigate logs report `loaded model from /path`. New
   detections start producing labels matching the model's classes.
5. **If Frigate+ subscription has lapsed**, the model file you
   already downloaded keeps working — Frigate+ models don't phone
   home. But re-training requires an active subscription.

### Scenario E — whole-cluster rebuild

Cluster destroyed and rebuilt from Git.

1. Frigate HelmRelease reconciled, pod starts; config is empty
   (or reverted to whatever's in the snapshot file).
2. NFS export on `security-storage` is independent of the
   cluster — still intact, clips + recordings preserved.
3. Frigate scans the existing recordings directory on first start
   and reconstructs its event index over hours (it walks every
   file). The pod is slow during this period.
4. Apply the most-recent good config from git snapshot per
   Scenario B.
5. Verify each camera reconnects.

## Adjacent direct-NFS workloads

These also need restoration thought:

- **Jellyfin media** — on `beast:/mnt/mass_storage/Movies` (and
  related). RAID6 protects against disk loss; cluster rebuild
  doesn't affect the data. Restore = Jellyfin scans the existing
  library, indexes media. Watch state is in the Jellyfin CNPG
  cluster (see `cnpg_restore.md`).
- **Immich originals** — under `media/immich`, also direct-NFS
  for the originals bucket. Offsite-covered for Immich
  specifically (see `offsite_recovery.md`).
- **Music libraries** (MP3s on beast) — direct-NFS, RAID6
  protected. No specific Frigate-shape DR; treat as Jellyfin.

## Gotchas

- **XFS `prjquota` mount option** is required for per-camera
  retention. If a remount drops it (e.g., re-export without the
  flag), Frigate's quota enforcement silently does nothing.
- **Frigate's sqlite event index** is in the PVC, not on NFS. PVC
  loss → event index loss → events not searchable in UI until
  Frigate re-indexes from recordings.
- **`frigate-snapshot` CronJob is broken since 2026-05-08** — `apk
  add git openssh` fails under egress restrictions. Workaround:
  read live config directly with `kubectl exec frigate-0 -c app
  -- cat /config/config.yml`. Pre-baked image is the real fix.
- **HA integration** — the Frigate↔HA integration requires MQTT
  - the HA add-on; both need to be healthy. If HA's broker is
  down, Frigate fires detections but HA doesn't see them.

## Verification checklist

After any recovery:

- [ ] All 7+ cameras connected (UI → Camera Status)
- [ ] Recent clips being written (`ls -la <NFS>/<camera>/...`)
- [ ] Model loaded (Frigate logs report load + class count)
- [ ] HA → Frigate integration shows recent events
- [ ] No Frigate-related alerts firing
- [ ] XFS prjquota enforcement working (`xfs_quota -x -c "report"
  /mnt/<export>` reports per-camera usage)

## See also

- `cnpg_restore.md` — Jellyfin watch-state DB restore
- `offsite_recovery.md` — Immich (related direct-NFS app)
- `storage-class.instructions.md` — direct-NFS tier rationale
- Memory: `[[project_frigate_snapshot_cronjob_broken]]` — snapshot
  CronJob recovery
- Memory: `[[reference_frigate_zone_semantics]]` — zone-related
  detection gotchas
