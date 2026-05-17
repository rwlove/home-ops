# Importing an Immich DB backup into a new CNPG database

This is the **alternate** Immich DB restore path — for when you have an Immich-format SQL dump (from Immich's built-in backup) rather than a Barman-cloud backup. For the standard Barman recovery path used after a cluster rebuild, see [Cluster Rebuild](cluster_rebuild.md#cnpg-recovery-from-garage); for the offsite-S3 path, see [Offsite Recovery](offsite_recovery.md).

## Procedure

1. **Create the target database**:

   ```sh
   kubectl cnpg -n databases psql <cluster-name> -- -c 'CREATE DATABASE immich;'
   ```

2. **Confirm you're against the primary** (writes go to the primary; replicas are read-only):

   ```sh
   kubectl cnpg -n databases status <cluster-name> | grep 'Primary instance:'
   ```

3. **Stream the dump in** via the [Immich restore command](https://immich.app/docs/administration/backup-and-restore). The `sed` rewrite is required because Immich uses extensions whose `search_path` needs to be set explicitly during restore:

   ```sh
   gunzip < "immich-db-backup-1735455600013.sql.gz" \
     | sed "s/SELECT pg_catalog.set_config('search_path', '', false);/SELECT pg_catalog.set_config('search_path', 'public, pg_catalog', true);/g" \
     | kubectl cnpg -n databases psql <cluster-name> --
   ```

4. **Verify** the row counts after restore (replace `<dump-row-count>` with what you'd expect from the source):

   ```sh
   kubectl cnpg -n databases psql <cluster-name> -- -d immich -c 'SELECT COUNT(*) FROM assets;'
   ```

## When to use this vs the Barman path

| Situation | Use |
|---|---|
| Cluster rebuild, Barman backups in Garage | [Cluster Rebuild → CNPG recovery from Garage](cluster_rebuild.md#cnpg-recovery-from-garage) |
| Total loss, only S3 offsite backup survives | [Offsite Recovery → Step 4: Restore the database via Barman](offsite_recovery.md) |
| Have a `pg_dump` from inside Immich (admin → "Job Status" → backup) | **This doc.** |
| Migrating Immich data between environments | This doc. |
