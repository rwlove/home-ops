# Windmill workflow source

These TypeScript scripts are the source-of-truth for the 7 Windmill
flows that replaced n8n in the `lovenet` workspace.

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

| env | source 1P item / field |
|---|---|
| `LANGGRAPH_APPROVAL_SIGNING_KEY` | `langgraph-agents.LANGGRAPH_APPROVAL_SIGNING_KEY` |
| `PUSHOVER_USER_KEY` | `langgraph-agents.PUSHOVER_USER_KEY` |
| `PUSHOVER_APP_TOKEN` | `langgraph-agents.PUSHOVER_APP_TOKEN` |
| `ZULIP_BOT_EMAIL` | `zulip-n8n-bot.ZULIP_BOT_EMAIL` |
| `ZULIP_BOT_API_KEY` | `zulip-n8n-bot.ZULIP_BOT_API_KEY` |
| `ROB_ZULIP_USER_ID` | `zulip-n8n-bot.ROB_ZULIP_USER_ID` |
