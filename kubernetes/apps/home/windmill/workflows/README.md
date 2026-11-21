# Windmill workflow source

These TypeScript scripts are the source-of-truth for the Windmill
flows in the `lovenet` workspace.

Runtime lives in the Windmill DB; this directory is the *checked-in
canonical text*. There is **no automatic git → Windmill sync**: after a
change merges, deploy each edited file as a new version of its script
(Windmill MCP `updateScript`, or `POST /api/w/lovenet/scripts/create`
with `parent_hash` set to the current hash) with **`language: bun`**.
Test first as a preview (`jobs/run/preview`, `language: bun`), and
confirm the deployed version's lock is non-null before relying on it.

## Language: Bun (not Deno)

All scripts are Windmill **`bun`** TypeScript (migrated from `deno`
2026-11, home-ops#14945). Upstream ships the Deno runtime only on the
`windmill-full` images, so a `deno` script fails on the standard image
with "Deno is not installed on this worker". Keep new scripts on Bun:

- Env: `process.env.NAME` — never `Deno.env.get`.
- HTTP: the global `fetch` + `AbortSignal.timeout`.
- Raw TCP (the SMTP helper): `node:net`, not `Deno.connect`.
- npm packages: bare specifier with a pinned version, e.g.
  `import * as wmill from "windmill-client@1.527.0"` (no `npm:`
  prefix — that is Deno syntax). Windmill resolves it and writes the
  script lock at deploy time.

## Convention

- One `.ts` file per workflow.
- Filename matches the Windmill script path basename — e.g.
  `workaround-watcher.ts` → `f/lovenet/workaround-watcher`.
- Each file is self-contained: no shared modules. Windmill's "trigger
  the script via webhook" model expects each script to be standalone.
- Secrets are read from environment variables that the worker pod
  has via `secretKeyRef` → `windmill-workflows-secret` (k8s Secret
  materialized by ExternalSecret from 1P). Do not embed secrets
  inline.

## Secrets the workers consume (via env)

| env | source |
|---|---|
| `SMTP_HOST` / `SMTP_PORT` | literal (`smtp-relay.home.svc.cluster.local` / `2525`) — plain env in the HelmRelease |
| `NOTIFY_EMAIL_FROM` / `NOTIFY_EMAIL_TO` | literal (`windmill@${SECRET_DOMAIN}` / `admin@${SECRET_DOMAIN}`) — plain env in the HelmRelease |
| `PAPERLESS_TOKEN` | `paperless.mcp_token` (shared with paperless-mcp) |
| `LIGHTRAG_API_KEY` | `lightrag.api_key` (shared with the ai-ns lightrag ExternalSecret) |
| `WINDMILL_TOKEN` | `windmill.windmill_api_token` (failure-watcher self-introspection) |

`restricted-name-audit` reads its pattern from the Windmill **secret
variable** `f/lovenet/restricted_name_regex` (a JS RegExp source,
compiled case-insensitive), not from env. It lives only in Windmill so the
restricted-tier name list never lands in this public repo; edit it in the
Windmill UI / API.

## Notifications

The watcher workflows (`windmill-failure-watcher`, `workaround-watcher`,
`restricted-name-audit`)
notify by **email**. Each speaks plaintext
SMTP to the in-cluster `smtp-relay` (home ns, maddy) on port 2525,
which relays out via Mailgun. The small SMTP helper is duplicated inline
in each workflow (self-contained, per the convention above).

The earlier ntfy push + Zulip DM paths — including the langgraph
interactive approve/reject/defer action buttons — were retired when the
langgraph fleet was decommissioned (2026-07-06) and ntfy + Zulip were
removed from the notification stack.
