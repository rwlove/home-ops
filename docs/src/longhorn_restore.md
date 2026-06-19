# Longhorn Restore Runbook

Restore procedure for any Longhorn-backed PVC after backup-target
loss, cluster wipe, or accidental volume deletion. Paper runbook —
verify against `kubectl -n longhorn-system ...` before executing.

## Cluster baseline

- Deployment: `kubernetes/apps/longhorn-system/longhorn/`
- Backup target (set in HelmRelease):
  `nfs://beast:/mnt/mass_storage/longhorn-backups`
- Recurring jobs
  (`kubernetes/apps/longhorn-system/longhorn/config/recurring-jobs.yaml`):
  - `daily-snapshots` — every day at 00:00, retain 7
  - `weekly-backups` — Saturdays at 00:00, retain 4
  - `monthly-backups` — 1st of month at 00:00, retain 6
  - `weekly-filesystem-trim` — Sundays at 03:00
- Storage tier (per `storage-class.instructions.md`):
  cluster-loss-survivable. Used for irreplaceable state only.

## When to use this

- A Longhorn-backed PVC's data is corrupt or wrong, and you have a
  recent snapshot or backup.
- A Longhorn-backed PVC was accidentally deleted.
- Cluster rebuild restored from Git; Longhorn was redeployed
  fresh and needs to ingest the backup target.

## Backup target inventory

Backups land on `beast:/mnt/mass_storage/longhorn-backups` (RAID6,
tolerates 2-disk loss). The path is mounted into the
longhorn-instance-manager pods.

Listing what's available:

```sh
kubectl -n longhorn-system exec -it deploy/longhorn-ui -- sh
# Or via UI: longhorn.${SECRET_DOMAIN} → Backup
```

Or directly from beast:

```sh
ssh beast 'ls /mnt/mass_storage/longhorn-backups/backupstore/volumes/'
```

Each volume has its own subdirectory; backup chains are stored as
.cfg + diff files. Don't hand-edit.

## Scenario A — restore from a recent snapshot

A volume's data is wrong but the Longhorn volume itself is
healthy. Restore from a daily/weekly snapshot.

1. Identify the volume: `kubectl -n longhorn-system get volumes`
   or via the UI.
2. List snapshots: UI → Volume → Snapshots tab. Look for the most
   recent good snapshot (timestamp before the bad event).
3. **Detach the volume from its workload first** (scale the
   consumer pod to 0). Longhorn does not snapshot-revert while
   attached.
4. UI → Volume → Snapshots → select snapshot → Revert.
5. Re-attach: scale the consumer back to 1.
6. Verify app starts cleanly.

## Scenario B — restore from a Longhorn backup

The volume is unavailable or the cluster was rebuilt. Restore from
the `nfs://beast` backup target.

1. Verify the backup target is accessible:

   ```sh
   kubectl -n longhorn-system get backuptarget default
   # Should be Available=True
   ```

2. List backups (UI → Backup), find the volume + backup version
   you want.
3. **Restore creates a new volume.** Old volume (if any) stays —
   delete it separately.
4. UI → Backup → backup-name → Restore. Set new volume name
   (typically reuse the old PVC name to make swap simpler).
5. After restore completes (can be slow for large volumes — Immich
   thumbs PVC takes 30+ minutes), edit the PV/PVC binding to point
   at the new volume.

   Easier path: name the restore volume something new, then update
   the consumer's PVC `volumeName` to bind.

6. Scale the consumer pod up.
7. Verify app starts cleanly.

## Scenario C — full Longhorn rebuild (cluster recreated)

The cluster was destroyed and rebuilt from Git. Longhorn is
deployed fresh with no volumes. The backup target NFS is intact.

1. Confirm the HelmRelease's `backupTarget` points at
   `nfs://beast:/mnt/mass_storage/longhorn-backups`. Default in
   the values; verify:

   ```sh
   flux get helmrelease -n longhorn-system longhorn -o yaml | yq '.spec.values.backupTarget'
   ```

2. Wait for `kubectl -n longhorn-system get backuptarget default
   -o yaml` to show `Available=True`. May take 1-2 minutes.
3. The UI's Backup tab now lists all previously-stored backups.
4. For each app that needs restore: follow Scenario B per volume.
   This is sequential per app; expect 1+ hours for full cluster
   data restore.

Apps that should be restored from Longhorn (per
`storage-class.instructions.md` rule 3 — irreplaceable state):

- `media/jellyfin` — watch state, user library, playlists
- `home/home-assistant` — device registry, automations state (the
  CNPG cluster covers DB; Longhorn covers HA's local store)
- `media/<media-library-app>` — library metadata
- Others — check the repo for `longhorn-pvc.yaml` files

```sh
find kubernetes/apps -name "longhorn-pvc.yaml" | sort
```

## Per-volume backup label requirements

Per the existing convention in this repo, every Longhorn volume
needs:

- Labels matching `recurring-jobs.yaml` group selector (default
  `recurring-job.longhorn.io/source: enabled` and
  `recurring-job-group.longhorn.io/default: enabled`)
- `unmapMarkSnapChainRemoved=enabled` set per-Volume to prevent
  snapshot-pinned slack
- `chmod 755` on `lost+found` if the app runs non-root (see
  memory `feedback_use_worktree_for_commit_bound_work` adjacent
  references)

If a restored volume doesn't show up in the recurring backups,
verify the labels.

## Gotchas

- **`backupTarget` NFS unreachable** → all backup operations stall;
  the UI shows "Backup target inaccessible". Check `ssh beast 'ls
  /mnt/mass_storage/longhorn-backups/'` from a worker node.
- **Restore fills the cluster's Longhorn disks faster than expected**
  — Longhorn replicas need ~3× the PVC size during restore (live
  copy + new replica + scratch). Verify disk space on
  worker nodes before kicking off a large restore.
- **Old volume still attached** — restore fails if the same
  volume name is in use. Detach (scale consumer to 0) or restore
  to a new name.
- **PV reclaim policy** — most Longhorn PVs in this repo are
  `Retain`. Manually deleting a PVC does not free the PV; you must
  delete the PV separately. This is intentional protection but
  surprising during a restore.

## Verification checklist

After any restore:

- [ ] Volume reports `state: attached` and `robustness: healthy`
- [ ] Consumer pod running and reads/writes successfully
- [ ] Recurring backup job picks up the restored volume on next
  cron (verify in UI's Backup tab after 24h)
- [ ] No Longhorn alerts firing

## See also

- `rook_ceph_dr.md` — Longhorn's storage is independent of Ceph
- `cluster_rebuild.md` — full cluster recovery context
- `storage-class.instructions.md` — when to put data on Longhorn
  vs ceph-block vs Garage vs NFS
- Memory: `[[reference_longhorn_xfs_repair]]` — repair recipe for
  corrupted XFS Longhorn volumes
- Memory: `[[reference_rook_ceph_csi_plugin_tolerations_combined]]`
  — adjacent cross-CSI gotcha
