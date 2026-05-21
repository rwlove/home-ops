# Cluster Rebuild Runbook

End-to-end recovery procedure for losing the cluster and rebuilding it
from Git + 1Password + Garage. Read this before you need it.

## What survives a rebuild

| Survives | Lives on | Notes |
|---|---|---|
| Git repo | GitHub | Source of truth for everything below `kubernetes/` |
| 1Password vault | Cloud | Holds every `op://` reference in `bootstrap/resources.yaml.j2` and every `ExternalSecret` |
| Garage S3 buckets (`postgres-*-backup/`) | NFS via `${NFS_HOST_0}:/mnt/kubernetes/garage/data` | This is what CNPG recovery depends on. Verify NFS host is healthy *before* destroying anything. |
| NFS-backed app data | `${NFS_HOST_0}` / `${NFS_HOST_2}` | Media libraries, garage data + meta, anything mounted via NFS PVs |

## What does NOT survive

- **Ceph OSDs** — `init/destroy-cluster.sh` wipes `/dev/ceph-*` on every
  node. Anything on `ceph-block` (every CNPG `PGData`, app config
  PVCs) is gone.
- **Longhorn replicas** — `/var/lib/longhorn/*` is wiped on every
  worker. Re-replicated data is lost; non-replicated data on a single
  node may survive but treat it as gone.
- **etcd** — `/var/lib/etcd/` wiped on every control-plane node.
- **kube-vip lease, certificates, kubeconfigs** — regenerated.

The only PG durability layer that survives is the Barman base
backups + WAL stored in Garage. Verify `${NFS_HOST_0}` is up and the
`/mnt/kubernetes/garage/{data,meta}` exports are intact before you
run `destroy-cluster.sh`.

## Preflight (do before destroying anything)

1. **Confirm NFS host health** for both `${NFS_HOST_0}` (Garage +
   media) and `${NFS_HOST_2}`. `showmount -e <host>` should list the
   exports.
2. **Confirm a recent CNPG backup exists** for every cluster you care
   about. Either inspect `s3://postgres-*-backup/<serverName>/base/`
   in the Garage WebUI, or run an immediate backup first:

   ```bash
   ./tools/onetime-cnpg-backup.sh        # uses postgres cluster name
   ```

   For per-app, edit `kubernetes/apps/databases/cloudnative-pg/config/onetimebackup.yaml`
   to target the cluster (e.g. `postgres-immich`) and apply.
3. **Confirm 1Password Connect creds are current** — the `kubernetes`
   vault must contain `1password/OP_CREDENTIALS_JSON`,
   `1password/OP_CONNECT_TOKEN`, and the `homelab` item with
   `SECRET_DOMAIN`, `EMAIL`, `NFS_HOST_*`.
4. **Note any suspended Flux resources** — `flux get all -A | grep -i
   suspend`. Suspended state is *not* in Git; you'll lose the
   suspension on rebuild and Flux will reconcile those apps. Decide
   per app whether that's OK.
5. **Check for in-flight `disable-<app>` commits** on `main` (see
   `CLAUDE.md` → "Flux suspend / disable workflow"). Don't rebuild
   while one of those is pending.

## Tear down

Only needed if reusing the same hardware. Skip on a fresh fleet.

```bash
./init/destroy-cluster.sh    # from your laptop, NOT a control plane
```

This drains every node, runs `kubeadm reset`, wipes Ceph devices via
`/root/ceph-cleanup.sh` on each node, and clears
`/var/lib/{etcd,kubelet,longhorn,rook}`.

## Bootstrap

1. **Create the cluster** (run on `master1`):

   ```bash
   ./init/create-cluster.sh
   ```

   Sets up kube-vip, runs `kubeadm init`, joins masters 2/3 and all
   workers, labels Longhorn-eligible nodes, makes `master1`
   schedulable.

2. **Initialize the cluster** (run on your laptop):

   ```bash
   ./init/initialize-cluster.sh
   ```

   Pulls the kubeconfig from `master1`, creates the bootstrap
   namespaces, runs `just -f bootstrap/mod.just resources` (renders
   1Password-backed secrets), applies CRDs from
   `bootstrap/helmfile.d/00-crds.yaml`, then `helmfile sync` for
   `01-apps.yaml` (Cilium, CoreDNS, cert-manager, external-secrets,
   1Password Connect, Flux operator + instance).

3. **Remove the static kube-vip manifest** (run on your laptop):

   ```bash
   ssh root@master1 rm /etc/kubernetes/manifests/kube-vip.yaml
   ```

   Once Flux brings up the in-cluster kube-vip, the static pod is
   redundant and will fight for the VIP.

## Verify GitOps

Flux should now be reconciling everything under `kubernetes/`. Watch
for the bootstrap chain:

```bash
flux get sources git -A
flux get ks -A          # kustomizations should turn Ready=True
flux get hr -A
kubectl get events -A --sort-by=.lastTimestamp | tail -50
```

Order to expect things to come up:

1. `flux-system` → cilium, coredns, cert-manager, external-secrets, 1Password Connect
2. `rook-ceph` → operator, then `rook-ceph-cluster` (slow; wait for OSDs to form)
3. `databases/cloudnative-pg` (operator + barman-cloud plugin)
4. `storage/garage` (NFS-backed; should come up quickly once
   external-secrets is alive)
5. The per-cluster CNPG `Cluster` resources, which trigger recovery
   (next section)
6. App workloads

## CNPG recovery from Garage

This is the load-bearing part of the rebuild. **Do not skip the
verification steps.**

### How it works

Each CNPG cluster under
`kubernetes/apps/databases/cloudnative-pg/config/<app>/` has three
files that together drive recovery:

- `objectstore.yaml` — `barmancloud.cnpg.io/v1 ObjectStore` pointing
  at `http://garage.storage.svc.cluster.local:3900` with bucket
  `s3://postgres-<app>-backup/`. Credentials come from the per-app
  `*-secret` (synced from 1Password by the app's
  `externalsecret.yaml`).
- `cluster.yaml` — the `postgresql.cnpg.io/v1 Cluster`. Two relevant
  blocks:
  - `spec.bootstrap.recovery.source: source` — tells CNPG to
    initialize a brand-new cluster by restoring from `externalClusters[0]`.
  - `spec.externalClusters[].plugin.parameters` — references the
    `ObjectStore` by `barmanObjectName` and `serverName`.
- `scheduledbackup.yaml` — the recurring backup that produced what
  you're now restoring.

`bootstrap.recovery` **only fires on first cluster creation**. If a
PVC already exists for the cluster, CNPG will resume from local data,
not from Garage. That's why teardown wipes Ceph — it forces a true
first-creation on the way back up.

### State of the bootstrap blocks

CNPG clusters fall into three groups today. Verify with:

```sh
for f in kubernetes/apps/databases/cloudnative-pg/config/*/cluster.yaml; do
  name=$(basename "$(dirname "$f")")
  if   grep -qE '^[^#]*recovery:' "$f"; then echo "PRE-ARMED:  $name"
  elif grep -qE '^\s*#\s*recovery:' "$f"; then echo "COMMENTED:  $name"
  else                                       echo "NO_RECOVERY:$name"
  fi
done
```

**Pre-armed** — `bootstrap.recovery` is un-commented. No-op while the cluster exists; ready to recover on next bootstrap:

- `atuin`, `home-assistant`, `immich`, `lldap`, `paperless`

**Commented** — block exists in the file but is commented out. Uncomment before the relevant Flux Kustomization runs on rebuild:

- `cutvideo`, `medikeep`, `netbox`, `pump`, `romm`, `sparkyfitness`, the media-pull-stack apps

**No recovery block at all** — these clusters have never been wired for Barman recovery. **Add a `bootstrap.recovery` block (and confirm an `ObjectStore` + `ScheduledBackup` exist) before any rebuild that needs them to survive:**

- `authelia`, `av1corrector`, `khoj`, `langgraph-checkpoints`, `langgraph-memory`, `n8n`, `nametag`, `videodupfinder`, `zulip`

For commented and no-recovery sets, edit `cluster.yaml` to include:

```yaml
bootstrap:
  recovery:
    source: source
```

Commit and push so Flux sees it. (For a planned rebuild you can do
this in advance on a branch and merge right before destroy.)

### Per-cluster recovery (no manual steps required if pre-armed)

1. Wait for `cnpg` operator + `plugin-barman-cloud` HelmReleases to
   be ready, and the `garage` Kustomization to be Ready.
2. The per-app Flux Kustomization (e.g. `cnpg-immich`) creates the
   `ObjectStore` + `Cluster` + `ScheduledBackup`.
3. CNPG sees no PGData PVC, runs the bootstrap recovery job, which
   pulls the most recent base backup from `s3://postgres-<app>-backup/`
   and replays WAL up to the latest archived segment.
4. Cluster transitions through `Setting up primary` → `Cluster in
   healthy state`. Replicas are then created normally on `ceph-block`.

Watch one cluster at a time:

```bash
kubectl -n databases get cluster -w
kubectl -n databases describe cluster postgres-immich | tail -50
kubectl cnpg -n databases status postgres-immich
kubectl -n databases logs -l cnpg.io/jobRole=full-recovery -f
```

### Point-in-time recovery (PITR)

To recover to a specific moment instead of "latest WAL", add a
`recoveryTarget` block — see the commented template in
`config/nextcloud/cluster.yaml`:

```yaml
  bootstrap:
    recovery:
      source: source
      recoveryTarget:
        targetTime: "2025-12-01 00:00:00.00000+00"
```

Apply, wait for recovery to complete, then **remove the
`recoveryTarget`** in a follow-up commit so future bootstrap attempts
default to "restore latest".

### Verifying a recovery

```bash
kubectl cnpg -n databases status <cluster-name>
kubectl cnpg -n databases psql <cluster-name> -- -c '\l'
kubectl cnpg -n databases psql <cluster-name> -- -c 'SELECT pg_is_in_recovery(), now();'
```

Confirm row counts on a critical table per app (e.g. `assets` for
immich, `documents_document` for paperless). Compare against your
last known production count if you have one.

### Recovery failure modes

- **`ObjectStore` not Ready** → check the per-app secret synced
  (`kubectl -n databases get externalsecret`), and the Garage
  endpoint resolves (`kubectl -n databases run -it --rm curl
  --image=curlimages/curl -- curl -v http://garage.storage.svc.cluster.local:3900`).
- **Recovery job pulls nothing** → bucket is empty or `serverName`
  doesn't match. Check the Garage WebUI for
  `s3://postgres-<app>-backup/<serverName>/base/`.
- **Recovery job hangs on WAL replay** → likely a single corrupt WAL
  or a base backup older than the oldest WAL retained. Switch to
  PITR with `targetTime` set just past the base backup timestamp.
- **Cluster goes healthy but app reports missing rows** → you
  recovered to the latest WAL archive, which may lag behind the last
  in-flight transactions. Expected. WAL is archived on
  `archive_command`, not synchronously.
- **Cluster keeps recreating with no bootstrap** → the PVC from a
  previous attempt is still around. Delete the `Cluster`, then the
  PVCs (`kubectl -n databases delete pvc -l cnpg.io/cluster=<name>`),
  then let Flux re-create.

## Restoring app-level data that bypasses CNPG

The Immich photo library, Jellyfin media, etc. live on Longhorn or
NFS, *not* in CNPG. Recovery for those:

- **NFS-backed** (most media, Garage data) → no action; data was
  never on the cluster.
- **Longhorn `numberOfReplicas: 1`** → gone. Restore from whatever
  external backup you have for that PV (none, for media that's
  re-acquirable).
- **Longhorn `numberOfReplicas: 2+`** → also gone after a full
  destroy; the wipe runs on every node. Restore from external backup.

**Offsite-backed apps** (Immich photo library and Paperless documents) ship encrypted to AWS S3 Glacier Deep Archive via per-app rclone CronJobs — files **and** Garage-stored Postgres backups. Recovery procedure is in [`offsite_recovery.md`](offsite_recovery.md). For everything else (media, Jellyfin libraries, etc.) there is no offsite layer today — that data is rebuildable from upstream or lost in a full-site loss.

## Post-rebuild tasks

1. **Update `KUBECONFIG` GitHub Actions secret**:
   GitHub → repo Settings → Secrets and Variables → Actions → edit
   `KUBECONFIG`:

   ```bash
   cat ~/.kube/config | base64 -w0
   ```

2. **Re-comment any `bootstrap.recovery` blocks you uncommented**
   for the rebuild, in a follow-up commit. They're no-ops once the
   cluster exists, but leaving an explicit recovery source in Git
   confuses future readers about whether the cluster is currently
   recovering.
3. **Re-suspend any Flux resources** that were suspended pre-rebuild
   (suspension state is not in Git).
4. **Verify the Ceph dashboard password**:

   ```bash
   ./tools/get-ceph-password.sh
   ```

5. **Watch one full backup cycle complete** for each CNPG cluster
   before considering the rebuild done. `kubectl -n databases get
   backup` should show a new entry per cluster.

## Related runbooks

- [Power Outage Recovery](power-outage.md) — kube-vip won't start;
  set the VIP manually on master1.
- [Initialization and Teardown](init_teardown.md) — prereqs and the
  bare command list.
- [Immich restore to new CNPG database](immich_cnpg.md) — only
  needed if recovering from a `pg_dump` instead of Barman.
