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

| env | source 1P item / field |
|---|---|
| `LANGGRAPH_APPROVAL_SIGNING_KEY` | `langgraph-agents.LANGGRAPH_APPROVAL_SIGNING_KEY` |
| `NTFY_URL` | literal template (`https://ntfy.${SECRET_DOMAIN}`) |
| `NTFY_WRITE_TOKEN` | `ntfy.NTFY_WRITE_TOKEN` |
| `ZULIP_BOT_EMAIL` | `zulip-windmill-bot.ZULIP_BOT_EMAIL` |
| `ZULIP_BOT_API_KEY` | `zulip-windmill-bot.ZULIP_BOT_API_KEY` |
| `ROB_ZULIP_USER_ID` | `zulip-windmill-bot.ROB_ZULIP_USER_ID` |
| `PAPERLESS_TOKEN` | `paperless.mcp_token` (shared with paperless-mcp) |
| `LIGHTRAG_API_KEY` | `lightrag.api_key` (shared with the ai-ns lightrag ExternalSecret) |

## Approval-token signing (pre-sign at post-time)

`langgraph-approval-post.ts` and the inline `postApproval` in
`langgraph-inbox.ts` pre-sign three HMAC-SHA256 approval tokens
(approve / reject / defer) when the agent pauses and embeds them in
the ntfy push's action buttons. Tapping a button on the phone POSTs
the matching token to `https://langgraph.${SECRET_DOMAIN}/approval`
(exposed by `kubernetes/apps/ai/langgraph-agents/app/route-approval.yaml`).

Single-use is enforced by langgraph-agents' paused-state machine: once
one verdict resumes the task, subsequent token POSTs return 409.

The Zulip emoji-reaction path in `langgraph-approval-receive.ts`
remains as a desktop fallback — it signs its own token at react-time.
