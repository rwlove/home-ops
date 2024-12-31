# Importing an Immich DB backup into a new CNPG database

1. Create the new Immich database

`kubectl cnpg -n databases psql postgres-16 -- -c 'CREATE DATABASE immich;'`

2. Ensure that you're woring with the primary CNPG instance

`kubectl cnpg -n databases status postgres-16 | grep "Primary instance:"`

3. Copy the database backup into the container's persistent storage (where there is likely enough room for it)

`kubectl -n databases cp ./immich-db-backup.sql.gz postgres-16-2:/var/lib/postgresql/data/`

4. Exec into the postgres container

`kubectl -n databases exec -it postgres-16-2 -- bash`

5. Run the [Immich restore command](https://immich.app/docs/administration/backup-and-restore)

`gunzip < "/path/to/backup/dump.sql.gz" | sed "s/SELECT pg_catalog.set_config('search_path', '', false);/SELECT pg_catalog.set_config('search_path', 'public, pg_catalog', true);/g" | psql --username=postgres`