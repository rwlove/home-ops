# CNPG Cluster Restore Template

Per-cluster restore procedure for any of the 28 CloudNativePG
`Cluster` resources under
`kubernetes/apps/databases/cloudnative-pg/config/<app>/` (each named
`postgres-<app>`). Paper runbook — verify against the actual app's
`cluster.yaml` + the `garage-<app>` ObjectStore before executing.

## When to use this

The PGData volume is gone (ceph-block PVC lost, ceph cluster wipe,
namespace deleted, etc.) but the Barman ObjectStore in Garage is
intact. **Most "the database is broken" cases are NOT this** — see
the gotchas section at the bottom first.

## What survives in Garage

Every CNPG cluster in this repo writes to a paired Barman
ObjectStore (`barmancloud.cnpg.io/ObjectStore`, named
`garage-<app>`) whose `destinationPath` is the Garage bucket
`s3://postgres-<app>-backup/`. Barman writes under a server
directory matching `serverName` (also `postgres-<app>-backup`), so
the objects live at:

- `s3://postgres-<app>-backup/postgres-<app>-backup/base/<backup-id>/`
  — full base backups (weekly `ScheduledBackup`; the first is seeded
  by hand at onboarding — there is no `immediate` backup)
- `s3://postgres-<app>-backup/postgres-<app>-backup/wals/` — WAL
  archive (continuous)

Lose Garage and you lose the recovery target. See
`garage_restore.md` for Garage substrate recovery.

## Recovery scenarios

### Scenario A — point-in-time recovery (PITR)

The PGData is intact but the database state is wrong (bad
migration, accidental `DELETE`, corrupted row). Restore to a
specific timestamp without losing the cluster.

1. Identify the recovery target:

   ```sh
   kubectl -n databases get cluster <app> -o yaml | yq '.spec.backup'
   # confirm the ObjectStore path
   kubectl -n databases logs <app>-1 -c postgres | grep -i 'archived'
   # find the latest WAL timestamp before the bad event
   ```

2. **Pre-armed** clusters (`atuin`, `home-assistant`, `immich`, `lldap`,
   `paperless` — see `cluster_rebuild.md` → *State of the bootstrap
   blocks* for the current arming lists) already ship a
   `bootstrap.recovery` stanza (`source: source`, with an
   `externalClusters[name: source]` reusing a `&params` plugin anchor —
   `barmanObjectName: garage-<app>`, `serverName: postgres-<app>-backup`).
   For a commented / no-recovery cluster you first add/uncomment that
   block. Then, for PITR, add a `recoveryTarget` to it in
   `kubernetes/apps/databases/cloudnative-pg/config/<app>/cluster.yaml`:

   ```yaml
   spec:
     bootstrap:
       recovery:
         source: source
         recoveryTarget:
           targetTime: "2026-05-20 14:30:00.00+00"
     externalClusters:
       - name: source
         plugin:
           name: barman-cloud.cloudnative-pg.io
           parameters:
             barmanObjectName: garage-<app>
             serverName: postgres-<app>-backup
   ```

   On a pre-armed cluster the `externalClusters` block is already present
   (shared via the `*params` anchor) — don't duplicate it.

3. **Delete the existing cluster** (destructive):

   ```sh
   kubectl -n databases delete cluster <app>
   # Wait for full teardown; CNPG operator drops the PVCs too.
   ```

4. Reconcile Flux:

   ```sh
   flux reconcile kustomization databases-cloudnative-pg-<app>
   ```

5. Watch the recovery:

   ```sh
   kubectl -n databases logs -l cnpg.io/cluster=<app>,role=primary -c postgres -f
   ```

   Recovery applies the base backup + WALs up to `targetTime`.

6. Verify and remove the `bootstrap.recovery` block (or leave it
   commented out for next time) in a follow-up PR.

### Scenario B — total cluster loss

PGData PVCs gone (ceph-block wipe, namespace nuked, etc.). Restore
the cluster from scratch from the latest base backup + WALs to
"now".

Same as Scenario A but with `recoveryTarget` set to `latest`
(omitting `targetTime`). For a **pre-armed** cluster (Scenario A
step 2) the `bootstrap.recovery` stanza is already in place, so
CNPG bootstraps from the ObjectStore on its own once the PGData PVC
is gone — just reconcile. For a commented / no-recovery cluster,
add the recovery + `externalClusters` block first (Scenario A
step 2), or it will bootstrap empty via `initdb` and lose the data.

Concretely:

1. Verify the ObjectStore is intact:

   ```sh
   kubectl -n databases get objectstore <app>
   # Check Garage bucket directly:
   AWS_ENDPOINT_URL_S3=https://s3.${SECRET_DOMAIN} \
     aws s3 ls s3://postgres-<app>-backup/postgres-<app>-backup/base/
   ```

2. Reconcile Flux to re-create the cluster CR. CNPG sees no
   PGData PVC and falls back to bootstrap-from-ObjectStore
   automatically (this is the default behavior with our setup).

3. Watch logs as above. Recovery completes when the cluster
   pod reports `database system is ready to accept connections`.

### Scenario C — Garage substrate gone

Both the database AND the Barman ObjectStore in Garage are lost.

**For most clusters**: full data loss — these apps' state isn't
shipped offsite. App reconstruction depends on the app:

- `atuin` — terminal history; gone, restart fresh
- `home-assistant` — see `home-assistant-config/` for what's
  in-Git and what's runtime-only
- media-pull-stack apps — full data loss is non-recoverable beyond
  re-scraping from external sources
- `medikeep` — full loss; manual reentry
- `pump`, `videodupfinder` — application-specific; mostly
  recoverable from source data
- `nametag`, `windmill` — workflow / metadata; full loss

**For Immich and Paperless**: see `offsite_recovery.md` — these
two have a separate offsite-backup pipeline to AWS Glacier Deep
Archive, restored independently.

## Per-cluster cheatsheet

Bucket = `postgres-<app>-backup`; Barman server directory inside it is
also `postgres-<app>-backup` (see *What survives in Garage* above).

| Cluster | Garage bucket | Special considerations |
|---|---|---|
| `postgres-atuin` | `postgres-atuin-backup` | None — terminal history is ephemeral |
| `postgres-home-assistant` | `postgres-home-assistant-backup` | Pair with HA backup (`.tar` in vault) for full restore |
| `postgres-immich` | `postgres-immich-backup` | Also covered by offsite Glacier — see `offsite_recovery.md` |
| `postgres-paperless` | `postgres-paperless-backup` | Also covered by offsite Glacier — see `offsite_recovery.md` |
| `postgres-langgraph-memory` | `postgres-langgraph-memory-backup` | memory-mcp's knowledge-graph backend (langgraph-agents, its former co-consumer, was decommissioned 2026-07-06 — sole owner is now memory-mcp) — 35+ entities; rebuildable but expensive |
| `postgres-windmill` | `postgres-windmill-backup` | Workflow definitions in Git; secrets in 1P — runtime state can be re-bootstrapped |
| Others (28 total) | `postgres-<app>-backup` | Follow the template above |

## Gotchas

- **CNPG `Cluster` `bootstrap.recovery` is honored only on initial
  bootstrap.** Once a cluster has PGData, editing the recovery
  block does nothing. Must delete the cluster first to force
  re-bootstrap.
- **CNPG operator must be running** before you delete a Cluster
  CR, or the finalizer hangs. Check
  `kubectl -n cnpg-system get pods` first.
- **Barman version compatibility** — the ObjectStore CR version
  must match the CNPG operator. Bumping CNPG without bumping
  Barman (or vice versa) can render backups unreadable.
- **PostgreSQL major version** in the ObjectStore must match the
  Cluster's `imageName`. PG16 base backup cannot bootstrap a PG17
  cluster.
- **Recovery is slow on large clusters.** Immich and Paperless can
  take 30+ minutes on the base-backup restore alone, before WAL
  replay starts.

## Verification checklist

After any restore:

- [ ] Cluster pod logs report `database system is ready`
- [ ] Application pod successfully connects (check app logs for
  ORM connection success)
- [ ] Smoke a representative query: `kubectl -n databases exec -it
  <app>-1 -- psql -c "SELECT count(*) FROM <known-table>"`
- [ ] App-level health endpoint returns 200
- [ ] Next `ScheduledBackup` runs successfully (catches the
  ObjectStore re-write path)

## See also

- `rook_ceph_dr.md` — restore underlying ceph-block first
- `garage_restore.md` — restore the Barman backup target
- `offsite_recovery.md` — Immich + Paperless Glacier-based restore
- `docs/src/immich_cnpg.md` — Immich-specific CNPG migration history
- Memory: `project_phase_a_embedder_bge_m3` — langgraph-memory KG
  re-embedding context
