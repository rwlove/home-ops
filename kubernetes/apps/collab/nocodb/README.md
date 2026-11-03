# nocodb — insurance policy register

Self-hosted [NocoDB](https://nocodb.com) relational backend for the **`lloyd`**
insurance / risk steward persona. It is the structured source of truth for
policy *facts* (carriers, policy numbers, coverage limits, deductibles,
premiums, renewal dates, claims); the source *documents* (declarations pages,
booklets, renewal notices, cards, EOBs) stay in Paperless. Internal-tier with
Restricted elements — see the data-classification note at the bottom.

## Shape

- **App:** `collab/nocodb`, app-template HelmRelease, image `nocodb/nocodb`
  (Docker Hub-only; CI allowlist + ZOT wildcard mirror cover it).
- **Metadata + all user tables:** the CNPG cluster `postgres-nocodb`
  (`databases` ns, ceph-block PGData, Barman→Garage DR, `@weekly` scheduled
  backup). NocoDB holds nothing durable on its own filesystem; the `data` PVC
  (ceph-block) is only attachments / local cache.
- **Access:** `nocodb.${SECRET_DOMAIN}` on the internal (LAN-only) gateway.
- **Agent bridge:** `ai/lloyd-mcp` (openapi-mcp over the NocoDB Data API v2)
  exposes the register to the in-cluster `lloyd` kagent agent. The agent binds
  the read + append surface only (no `deleteRecords`).

## Bring-up (one-time, after first reconcile)

Flux will deploy the app, but it can't function until these **human** steps are
done — the ExternalSecrets dangle `NotReady` until the 1Password items exist,
and the MCP bridge needs an API token that only exists after first login.

1. **1Password → `nocodb`** (Kubernetes vault), fields:
   - `NOCODB_DB_USER`, `NOCODB_DB_PASS` — the app's Postgres role (postgres-init
     creates it in the CNPG cluster).
   - `nc_auth_jwt_secret` — `openssl rand -hex 32`.
   - `GARAGE_AWS_ACCESS_KEY_ID`, `GARAGE_AWS_SECRET_ACCESS_KEY` — Garage S3 creds
     for the Barman backup (same pattern as every other `cnpg-app-database`).
   - (`cloudnative-pg` item already supplies `POSTGRES_SUPER_PASS`.)
2. Let Flux reconcile; confirm `postgres-nocodb` is healthy and the `nocodb` pod
   is `Running`.
3. **First login** at `nocodb.${SECRET_DOMAIN}`, create the NocoDB admin account.
4. **Create the base + tables** — base `insurance`, tables **Policies** and
   **Claims** (schema below).
5. **Generate an API token** (NocoDB → account → tokens) and store it in
   **1Password → `lloyd-nocodb`** field `api_token`. The `lloyd-mcp` bridge's
   ExternalSecret resolves to `xc-token:<api_token>`; once it's set the bridge
   pod starts and the kagent `lloyd` agent can reach the register.
6. **Seed** the existing policies from the Paperless declarations pages (Lloyd
   can do this: read each dec page, append a Policies row, record the Paperless
   backlink). The 2027 Red Hat benefits election is a ready first seed for the
   health / dental / vision lines.

## Register schema

**Policies** (one row per policy per effective period):
`carrier`, `line` (health|dental|vision|home|auto|umbrella|life|flood|other),
`policy_number`, `named_insureds`, `coverage_limits`, `deductible`, `premium`,
`premium_cadence`, `effective_date`, `renewal_date`, `endorsements`,
`agent_contact`, `paperless_doc` (declarations-page backlink).

**Claims** (one row per claim):
`policy` (link→Policies), `line`, `claim_number`, `status`, `adjuster`,
`date_of_loss`, `filed_date`, `amounts`, `paperless_doc`.

## Data classification

Internal with **Restricted** elements. Policy/member numbers, named insureds,
the property address, and all health-policy details never appear in a public
artifact or go to a remote public model. The register, Paperless, memory, and
replies to Robert are internal and fine.
