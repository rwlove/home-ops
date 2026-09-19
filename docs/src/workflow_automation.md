# Workflow Automation (Windmill)

> **Note:** The `langgraph-agents` fleet that once drove task intake, the
> approval loop, and 16 additional Windmill flows was decommissioned
> 2026-07-06 and removed entirely. This page documents only what runs
> today; the original design lives in git history if it is ever needed.

## Current state

- **No agent fleet.** `langgraph-agents` (the FastAPI service that used to
  live in the `ai` namespace) was deleted along with its Postgres
  checkpoints database, vault PVCs, and public routes. There is no
  `/inbox`, `/approval`, or `/admin/tasks`, and no automated Class A–D
  approval taxonomy.
- **Windmill is still deployed and runs 7 workflows** — none agent- or
  approval-related. All live under
  `kubernetes/apps/home/windmill/workflows/`:
  - `paperless-rag-fanout.ts` — unified Paperless → Qdrant **and** LightRAG
    ingest from a single pull (one watermark, one 15m cron). Supersedes the
    two split ingests below as of 2026-07-20.
  - `paperless-rag-ingest.ts` / `lightrag-rag-ingest.ts` — the original split
    Qdrant / graph ingests, now **superseded by the fan-out**; retained on
    disk with schedules paused for rollback.
  - `paperless-rag-tombstone.ts` / `lightrag-rag-tombstone.ts` — Qdrant +
    LightRAG tombstone sweeps (still separate).
  - `windmill-failure-watcher.ts` — Windmill self-introspection.
  - `workaround-watcher.ts` — GitHub `workaround`-labeled issue discovery;
    backs the upstream-watcher convention.
- **The approval flow no longer exists** — no Zulip `#approvals` stream, no
  ntfy/Pushover tap-to-approve loop, because there is no automated task
  pipeline producing anything to approve.
- **Critical AlertManager alerts still page Pushover directly.** That path
  predates and is unrelated to this decommission (the HolmesGPT AI
  investigation step that used to sit in front of it was removed in an
  earlier, separate pass, also landing 2026-07-06).
- **memory-mcp is unaffected** — the knowledge-graph MCP server backed by
  `postgres-langgraph-memory` is still live, still used by Claude Code and
  Open WebUI. It simply lost langgraph-agents as a consumer.
- **Known gap, not yet fixed:** the HA voice "inbox …" intent (a
  `rest_command` defined in the separate `home-assistant-config` repo) still
  POSTs to a Windmill webhook that used to forward to the now-removed
  `/inbox`, so it silently fails. A known follow-up, not resolved here.

If a replacement task-intake mechanism gets built, it belongs on this page —
this file stays the canonical spot for "how does work get from a human to an
agent" once that is true again.
