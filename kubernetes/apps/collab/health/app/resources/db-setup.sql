-- Source-of-record for the read-only DB objects the `health` app depends on.
-- Applied MANUALLY once to the pre-existing CNPG clusters (postgres-medikeep,
-- postgres-pump) — they are not fresh, so CNPG postInitApplicationSQL does not
-- re-run. Re-run this (idempotent) if a cluster is ever rebuilt from backup.
--
-- The health_ro role password lives in 1Password item "health" (RO_DB_PASS,
-- Kubernetes vault) and reaches PostgREST via the `health` ExternalSecret. Do
-- NOT put the password here. Views are owned by postgres, so health_ro reads
-- only these curated views, never base tables (verified: SELECT on public.*
-- denied). Views are generic: no medication names or dose values live in this
-- repo — they come from the DB at runtime (data-classification: restricted).

-- ======================= postgres-medikeep / medikeep =======================
CREATE SCHEMA IF NOT EXISTS api;

-- Blood pressure over time (patient_id 1 == the self record).
CREATE OR REPLACE VIEW api.v_bp AS
  SELECT recorded_date::date AS date, systolic_bp, diastolic_bp, heart_rate
  FROM public.vitals
  WHERE patient_id = 1 AND systolic_bp IS NOT NULL
  ORDER BY recorded_date;

-- Dose timeline: one Treatments row per dose period, labeled by treatment_name.
-- dosage is varchar ("N mg"), so the numeric mg is parsed here for charting.
CREATE OR REPLACE VIEW api.v_dose AS
  SELECT treatment_name, start_date, end_date,
         NULLIF(regexp_replace(dosage, '[^0-9.]', '', 'g'), '')::numeric AS dose_mg
  FROM public.treatments
  WHERE patient_id = 1 AND dosage IS NOT NULL AND dosage ~ '[0-9]'
  ORDER BY treatment_name, start_date;

-- health_ro: PostgREST authenticator + anon role. Read-only, views only.
-- CREATE ROLE health_ro LOGIN PASSWORD '<1Password health/RO_DB_PASS>'
--   NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT USAGE ON SCHEMA api TO health_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA api TO health_ro;

-- ========================== postgres-pump / pump ==========================
CREATE SCHEMA IF NOT EXISTS api;

-- public.weight is an append-only per-reading log (the bt-scale gateway
-- burst-posts the same reading as it settles, and distinct same-day weigh-ins
-- are kept). DISTINCT ON (date) collapses to one canonical row per date,
-- latest recorded_at wins — mirroring PUMP's own SelectW read semantics so
-- consumers (dashboard, Arnold, Crusher) see the same series PUMP's UI shows.
CREATE OR REPLACE VIEW api.v_weight AS
  SELECT DISTINCT ON (date) date, weight::numeric AS weight_lb
  FROM public.weight
  ORDER BY date, recorded_at DESC;

-- Training-plan queue progress — one row per program. Read by Arnold's
-- in-cluster kagent queue-check task (local-cron-arnold-queue-check, via pump-mcc
-- as health_ro) and available to the dashboard. cursor_position is the next-due
-- session's position; sessions_remaining counts the current session through the
-- end of the block. near_end flags "<= 8 sessions left" (~2 rotations/cycles) —
-- the low-queue threshold at which Arnold reminds Rob to run a check-in.
CREATE OR REPLACE VIEW api.v_plan_progress AS
  SELECT p.id AS program_id, p.name, p.cursor_position,
         c.total_sessions,
         (c.total_sessions - p.cursor_position + 1) AS sessions_remaining,
         (c.total_sessions > 0 AND (c.total_sessions - p.cursor_position + 1) <= 8) AS near_end
  FROM public.programs p
  CROSS JOIN LATERAL (
    SELECT count(*)::int AS total_sessions
    FROM public.prescribed_sessions s WHERE s.program_id = p.id
  ) c;

-- CREATE ROLE health_ro LOGIN PASSWORD '<same value>' NOSUPERUSER NOCREATEDB NOCREATEROLE;
GRANT USAGE ON SCHEMA api TO health_ro;
-- Covers all api.* views above (read-only). Re-run after adding a view.
GRANT SELECT ON ALL TABLES IN SCHEMA api TO health_ro;

-- Dose history is entered in MediKeep (Treatments: one row per dose period,
-- start_date/end_date/dosage). Backfill of blood pressure and other dose
-- histories is done by a local ETL from personal spreadsheets — those values
-- are restricted and are never committed to this repo.
