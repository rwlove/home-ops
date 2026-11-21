-- Source-of-record for the `arnold` role's privileges on postgres-pump / pump.
-- The ROLE (login + password) is declared in cluster.yaml (spec.managed.roles);
-- CNPG does not manage GRANTs, so apply this ONCE as superuser on the primary
-- and re-run (idempotent) if the cluster is ever rebuilt from backup:
--
--   kubectl -n databases exec -i $(kubectl -n databases get pod \
--     -l cnpg.io/cluster=postgres-pump,cnpg.io/instanceRole=primary -o name) \
--     -c postgres -- psql -d pump -v ON_ERROR_STOP=1 < grants-arnold.sql
--
-- Contract (Robert 2026-11-21, "read all, write plan only"):
--   * SELECT on every PUMP table (schema public, owned by role `pump`) and the
--     curated api.* views.
--   * INSERT/UPDATE/DELETE ONLY on the prescription layer: programs,
--     prescribed_sessions, prescribed_items (+ their SERIAL sequences).
--     DELETE is required: Arnold re-authors a plan by delete+reinsert; the FK
--     cascade into prescribed_session_completions runs as the table owner.
--   * No TRUNCATE, no DDL, not an owner, no CREATE on any schema.

-- Defence in depth: PUBLIC already lacks CREATE on public (PG15+); make it explicit.
REVOKE CREATE ON SCHEMA public FROM arnold;
GRANT CONNECT ON DATABASE pump TO arnold;
GRANT USAGE ON SCHEMA public, api TO arnold;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO arnold;
GRANT SELECT ON ALL TABLES IN SCHEMA api TO arnold;

GRANT INSERT, UPDATE, DELETE ON programs, prescribed_sessions, prescribed_items TO arnold;
GRANT USAGE, SELECT ON SEQUENCE programs_id_seq, prescribed_sessions_id_seq, prescribed_items_id_seq TO arnold;

-- Tables created by future PUMP migrations (run as `pump`) stay readable.
ALTER DEFAULT PRIVILEGES FOR ROLE pump IN SCHEMA public GRANT SELECT ON TABLES TO arnold;
