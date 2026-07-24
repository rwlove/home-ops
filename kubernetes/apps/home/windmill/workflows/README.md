# Windmill workflow source

These TypeScript scripts are the source-of-truth for the Windmill
flows that replaced in the `lovenet` workspace.

Runtime lives in the Windmill DB; this directory is the *checked-in
canonical text*. Push to Windmill via the API (see `tools/wmill-sync.sh`
or the inline curl in the README two levels up).

## Convention

- One `.ts` file per workflow.
- Filename matches the Windmill script path basename — e.g.
  `langgraph-cost-cap-watcher.ts` → `f/lovenet/langgraph-cost-cap-watcher`.
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

## Notifications

The watcher workflows (`windmill-failure-watcher`, `workaround-watcher`)
notify by **email**. Each speaks plaintext
SMTP to the in-cluster `smtp-relay` (home ns, maddy) on port 2525,
which relays out via Mailgun. The small SMTP helper is duplicated inline
in each workflow (self-contained, per the convention above).

The earlier ntfy push + Zulip DM paths — including the langgraph
interactive approve/reject/defer action buttons — were retired when the
langgraph fleet was decommissioned (2026-07-07) and ntfy + Zulip were
removed from the notification stack.
