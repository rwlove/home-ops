# Importing an Immich DB backup into a new CNPG database

1. Create the new Immich database

`kubectl cnpg -n databases psql <db name> -- -c 'CREATE DATABASE immich;'`

2. Ensure that you're woring with the primary CNPG instance

`kubectl cnpg -n databases status <db name> | grep "Primary instance:"`

3. Run the [Immich restore command](https://immich.app/docs/administration/backup-and-restore)

`gunzip < "immich-db-backup-1735455600013.sql.gz"| sed "s/SELECT pg_catalog.set_config('search_path', '', false);/SELECT pg_catalog.set_config('search_path', 'public, pg_catalog', true);/g" | kubectl cnpg -n databases psql <db name> --`
