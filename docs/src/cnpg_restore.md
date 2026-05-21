# CNPG Cluster Restore Template

Per-cluster restore procedure for any of the ~25 CloudNativePG
`Cluster` resources under
`kubernetes/apps/databases/cloudnative-pg/config/<app>/`. Paper
runbook — verify against the actual app's `cluster.yaml` +
`objectstore.yaml` before executing.

## When to use this

The PGData volume is gone (ceph-block PVC lost, ceph cluster wipe,
namespace deleted, etc.) but the Barman ObjectStore in Garage is
intact. **Most "the database is broken" cases are NOT this** — see
the gotchas section at the bottom first.

## What survives in Garage

Every CNPG cluster in this repo writes to a paired Barman
ObjectStore (`barmancloud.cnpg.io/ObjectStore`). The bucket layout
in Garage:

- `<cluster-name>/base/<backup-id>/` — full base backups (daily
  cadence on most clusters, scheduled by `ScheduledBackup`)
- `<cluster-name>/wals/` — WAL archive (continuous)

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

2. Edit `kubernetes/apps/databases/cloudnative-pg/config/<app>/cluster.yaml`
   to add `spec.bootstrap.recovery`:

   ```yaml
   spec:
     bootstrap:
       recovery:
         source: <app>
         recoveryTarget:
           targetTime: "2026-05-20 14:30:00.00+00"
     externalClusters:
       - name: <app>
         plugin:
           name: barman-cloud.cloudnative-pg.io
           parameters:
             barmanObjectName: <app>
             serverName: <app>
   ```

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
(omitting `targetTime`), or simply leave the cluster's normal spec
in place and CNPG will bootstrap from the ObjectStore on its own —
the `barmancloud.cnpg.io/ObjectStore` is the bootstrap source by
convention in this repo.

Concretely:

1. Verify the ObjectStore is intact:

   ```sh
   kubectl -n databases get objectstore <app>
   # Check Garage bucket directly:
   AWS_ENDPOINT_URL_S3=https://s3.${SECRET_DOMAIN} \
     aws s3 ls s3://cnpg-<app>/base/
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
- `radarr/sonarr/lidarr/prowlarr/recyclarr/suggestarr/soularr` —
  media-pull-stack apps; full data loss is non-recoverable beyond
  re-scraping from external sources
- `medikeep` — full loss; manual reentry
- `pump`, `sparkyfitness`, `videodupfinder`, `cutvideo` —
  application-specific; mostly recoverable from source data
- `nametag`, `medialyze`, `windmill` — workflow / metadata; full
  loss

**For Immich and Paperless**: see `offsite_recovery.md` — these
two have a separate offsite-backup pipeline to AWS Glacier Deep
Archive, restored independently.

## Per-cluster cheatsheet

| Cluster | Garage bucket | Special considerations |
|---|---|---|
| `atuin` | `cnpg-atuin` | None — terminal history is ephemeral |
| `home-assistant` | `cnpg-home-assistant` | Pair with HA backup (`.tar` in vault) for full restore |
| `immich` | `cnpg-immich` | Also covered by offsite Glacier — see `offsite_recovery.md` |
| `paperless` | `cnpg-paperless` | Also covered by offsite Glacier — see `offsite_recovery.md` |
| `langgraph-checkpoints` | `cnpg-langgraph-checkpoints` | Ephemeral graph state — accept full loss |
| `langgraph-memory` | `cnpg-langgraph-memory` | KG state — 35+ entities; rebuildable but expensive |
| `langfuse` | `cnpg-langfuse` | Trace history — accept loss, traces regenerate |
| `windmill` | `cnpg-windmill` | Workflow definitions in Git; secrets in 1P — runtime state can be re-bootstrapped |
| `zulip` | `cnpg-zulip` | Message history — important; verify backups before any ceph-block work |
| Others (25 total) | `cnpg-<app>` | Follow the template above |

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
