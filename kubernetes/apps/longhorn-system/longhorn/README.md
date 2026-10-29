# Longhorn — operational notes

The HelmRelease + StorageClasses live next to this file; tier-selection
guidance is in [`.agents/instructions/storage-class.instructions.md`](../../../../.agents/instructions/storage-class.instructions.md).
This README captures one runbook that pops up periodically.

## Fix `EACCES` / permission-denied errors on a Longhorn PVC

When you see something like `permission denied` on a directory the pod
expects to own — or the pod crashes during `chown` / startup — the
usual cause is one of:

- **UID drift.** The image's `USER` directive changed (often after a major bump), but the existing volume's files are owned by the prior UID.
- **`fsGroup` mismatch.** The HelmRelease specifies a `fsGroup` that doesn't include the directory mode/owner the app expects.
- **ext4 `lost+found` quirk.** Fresh ext4 Longhorn PVCs ship with `lost+found` owned `root:root` mode-700; non-root containers enumerating the volume's root directory hit `EACCES`. The fix for that specific symptom is `chmod 755 lost+found` once; see `project_ext4_lostfound_nonroot` memory.

The procedure below is for the UID-drift case — when the existing files need to be re-owned to match what the new pod expects. The image's running UID/GID is typically baked in (`568:568` for linuxserver.io images, `1000:1000` for bjw-s defaults, app-specific otherwise — check the container's `id` output before chowning).

### Procedure

1. **Scale the workload down**:

   ```sh
   kubectl scale -n <ns> --replicas=0 deploy/<name>
   # (or statefulset/<name>)
   ```

2. **Find the volume in the Longhorn UI.** Open Longhorn (port-forward `longhorn-frontend` in `longhorn-system` if you don't have ingress wired up), navigate to **Volume**, find the PV by name. It should be **Detached**.

3. **Attach to a node manually.** Click the volume name → top-right `⋮` → **Attach**. Pick any worker. Do **not** check the "Maintenance Mode" box.

4. **Note the device path.** The Volume Details screen shows `Attached Node` and an `Endpoint` like `/dev/longhorn/<pv-name>`.

5. **SSH to the attached node** and mount the block device to a temp mountpoint:

   ```sh
   ssh root@<node>
   mkdir -p /mnt/tmp
   mount /dev/longhorn/<pv-name> /mnt/tmp
   ```

6. **chown to the right UID/GID.** Confirm the target IDs from the image (e.g. `kubectl exec` into a running pod of the same image and `id`). Then:

   ```sh
   chown -R 568:568 /mnt/tmp/     # linuxserver.io default
   # or 1000:1000 for bjw-s default, or whatever `id` reported
   ```

7. **Unmount and detach.**

   ```sh
   umount /mnt/tmp
   ```

   Back in the Longhorn UI, top-right `⋮` → **Detach** → confirm.

8. **Scale the workload back up**:

   ```sh
   kubectl scale -n <ns> --replicas=1 deploy/<name>
   ```

### When NOT to use this

- **Newly-provisioned PVC with `lost+found` issue** — that's a one-time `chmod 755 lost+found`, not a full chown sweep. See the memory.
- **Symptom looks like "fresh PVC won't bind"** — that's usually a StorageClass / `volumeName` / capacity mismatch, not a permission issue. Check `kubectl describe pvc` first.
- **App documented as needing root** (some legacy images legitimately require it) — running the procedure won't help; the app needs `runAsUser: 0` and the security defaults in `helmrelease.security.md` overridden. See the memory for the canonical pattern.

## Static-Longhorn backend `Volume` CRs (`volumes/`)

Every app that uses the repo's static-Longhorn pattern (a binding-only
StorageClass + a static PV whose `csi.volumeHandle` is a readable name + a
PVC that binds by `volumeName`) also needs a backend `longhorn.io/v1beta2
Volume` object in `longhorn-system` whose `metadata.name` equals that
`volumeHandle`. Longhorn's CSI driver does **not** auto-create that object for a
statically-provisioned PV, so without it the PV attach fails `NotFound` and the
consumer pod hangs `Init:0/1`.

Those Volume CRs used to be hand-applied with `kubectl apply` and committed
nowhere. That was a disaster-recovery gap: a cluster rebuild recreates every
PV/PVC from Flux but **not** the backend volumes, so every static-Longhorn app
would come up `Init:0/1` until each CR was re-applied from memory. The
[`volumes/`](volumes/) directory closes that gap — one file per volume,
reconciled by the `longhorn-volumes` Flux Kustomization in
[`ks.yaml`](ks.yaml) (targets `longhorn-system`, `dependsOn: longhorn`).

### Rules for these files

- **Match the live Volume CR exactly.** The committed spec is built from the
  *live* object, not from the PV's `csi.volumeAttributes` — those attributes are
  only consulted at dynamic-provision time and routinely drift from the real
  volume (e.g. a PV attribute of `numberOfReplicas: "2"` on a volume Longhorn
  actually runs at 3). Adopting an attached, data-bearing volume **must** be a
  no-op; building from the PV attribute would make Flux patch the live replica
  count down. Verify with a server-side-apply dry-run
  (`kubectl apply --server-side --dry-run=server`) before trusting a new file.
- **Minimal, stable field set only.** Include the fields Longhorn persists as
  durable config (`accessMode`, `backingImage`, `backupCompressionMethod`,
  `backupTargetName`, `dataEngine`, `dataLocality`, `frontend`,
  `numberOfReplicas`, `size` as a quoted string, `staleReplicaTimeout`,
  `unmapMarkSnapChainRemoved`) plus the `backup-target` / `longhornvolume` /
  `recurring-job-group.longhorn.io/*` labels. **Omit** operationally-managed
  fields (`nodeID`, `image`, defaulted `snapshot*`/`replica*` settings) — Flux
  server-side-apply leaves fields it doesn't declare owned by longhorn-manager.
- **Omit `fromBackup` / `dataSource`.** These are create-time restore directives.
  On an existing volume they're inert, but committing a *fixed* `fromBackup`
  pointer is a DR footgun: a from-scratch rebuild would restore that specific
  (stale) backup instead of the latest. DR data recovery is a separate
  "restore the latest backup" operation, not a recreate-from-fixed-backup.
- **No `commonMetadata` on the Kustomization** — it would inject a label not on
  the live volumes, turning adoption into a metadata patch.

## Related

- [`.agents/instructions/helmrelease.security.md`](../../../../.agents/instructions/helmrelease.security.md) — securityContext defaults and the read-only-rootfs deviations.
- [`.agents/instructions/storage-class.instructions.md`](../../../../.agents/instructions/storage-class.instructions.md) — when Longhorn vs ceph-block vs Garage vs NFS.
- [Longhorn HelmRelease values](app/helmrelease.yaml) — backupTarget, recurring jobs, replica counts.
