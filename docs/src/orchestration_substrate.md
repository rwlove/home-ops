# Orchestration substrate

## Status

**Not built as of 2026-05-20** — but substrate is decided. HOMELAB-SPEC
Layer 5 references a task contract, queue substrate, DLQ, and per-mode
workers that this cluster does not yet implement. This document names
the gap, describes what exists today, and sketches the forward-looking
shape so we don't lose the specs while we wait.

**Substrate decision (2026-05-20):** CNPG LISTEN/NOTIFY — see
[`task_queue_substrate_design.md`](task_queue_substrate_design.md) for
the full evaluation. **That decision is moot as of 2026-07-06** — the
design picked LISTEN/NOTIFY specifically to piggyback on
`langgraph-agents`' own Postgres checkpointer, and both the fleet and
its `postgres-langgraph-checkpoints` database are gone. If/when this
substrate gets built, the evaluation needs to be redone against
whatever (if anything) plays the role langgraph-agents used to play.

## What exists today (2026-07-06)

Less than existed a day ago. The closest thing this cluster had to a
task pipeline — `langgraph-agents`, reached via Zulip DM and HA voice —
was removed entirely 2026-07-06 (`kubernetes/apps/ai/langgraph-agents/`,
`kubernetes/apps/ai/sync-receiver/`, the `postgres-langgraph-checkpoints`
CNPG cluster, and 16 Windmill workflows). See
[`workflow_automation.md`](workflow_automation.md) for the full
decommission detail. What's left:

- **AlertManager → Pushover** for critical alerts. There is no
  automated triage step — the `windmill-investigate` route/receiver
  and its HolmesGPT enrichment were removed 2026-07-06 (a separate,
  earlier decommission); AlertManager fires and pages Pushover
  directly.
- **HA voice + ntfy notifications** for Renee-facing surfaces.
  Currently end-user-driven, not agent-mediated.
- **Known gap, not yet fixed:** the HA voice "inbox …" intent (a
  `rest_command` in `home-assistant-config`) still POSTs toward the
  now-deleted `/inbox` endpoint and will silently fail. There is no
  DM-to-agent or voice-to-agent path anymore — Zulip Triager →
  Windmill → langgraph-agents `/inbox` and ntfy → langgraph approval
  endpoint are both gone along with the fleet they targeted.

None of these speak a common task contract. None have a DLQ. None
queue durably across a worker crash. None carry an OpenTelemetry
trace_id end-to-end.

## What would exist when built

Per HOMELAB-SPEC Layer 5:

- **Durable queue** with at-least-once delivery, DLQ, visibility
  timeouts that return crashed-worker tasks within minutes.
- **Ingress wrappers** that wrap raw user input (Open WebUI, HA voice,
  Android, AlertManager) in a task envelope before enqueueing. Raw
  surface input never reaches an agent.
- **Per-mode workers** for the modes defined in HOMELAB-SPEC Layer 4:
  planner, executor, reviewer, guardian, observer, historian,
  upstream-watcher, router. Modes compose with personas
  (`~/.claude-personal/agents/*.md`).
- **Observability**: OpenTelemetry traces follow `trace_id` from
  ingress through every mode hop to PR / CI / Flux reconcile /
  historian summary. Structured logs carry `trace_id`, `task_id`,
  `mode`, `persona`. Grafana dashboard with one panel per task.
- **Human-in-the-loop queue** with `ttl` on destructive tasks. On
  expiry the task does *not* auto-execute — it expires and notifies
  Rob with a summary. Urgent tasks page; normal tasks wait.
- **DLQ → review queue**: DLQ entries become tasks for Rob's review,
  not silent failures.

## Task envelope (forward-looking)

When the substrate exists, every task carries:

- `id` — ULID
- `trace_id` — OpenTelemetry-compatible
- `origin` — `open-webui` | `ha-voice` | `android` | `observer` |
  `scheduled` | `manual`
- `requester` — `rob` | `renee` | `system`
- `intent` — natural-language string
- `priority` — `low` | `normal` | `high` | `urgent`
- `destructive` — bool, planner sets/confirms
- `idempotency_key` — task handlers must be safe under at-least-once
  delivery; this is how
- `ttl` — after which guardian-queued tasks expire and notify Rob
- `retry_policy`
- `data_tier` — `public` | `internal` | `restricted`
  (see `.agents/instructions/data-classification.md`)

Tasks without this envelope are rejected at ingress.

## Renee allowlist (forward-looking)

When the substrate exists, Renee's intents are auto-approved when
they fall into these categories:

- Media playback (Jellyfin, Music Assistant, Kodi)
- Lighting
- Climate
- Scenes
- Locks-unlock-when-already-home (NOT from-away)

Anything else from Renee routes to Rob's queue with a Renee-originated
tag. Renee never sees admin output, stack traces, or restricted-tier
data.

Start narrow — easier to widen than retract.

## Observer and Guardian modes (deferred)

HOMELAB-SPEC Layer 4 defines Observer (watches cluster health, files
tasks) and Guardian (owns human-approval gate with TTL). Both depend
on the queue substrate. Observer mode is fully aspirational today —
AlertManager fires and pages Pushover directly with no automated
triage step (the HolmesGPT interim substitute was removed 2026-07-06).
Guardian responsibilities live in Rob (the human); there is currently
no automated pipeline generating approval requests for Rob to respond
to (the ntfy/Zulip approval loop was langgraph-agents-specific and was
removed with the fleet 2026-07-06).

## Token / cost budget (deferred)

HOMELAB-SPEC Layer 6 asks for a daily + weekly MAX-phase budget. Not
set today. Plan: ship the Grafana dashboard panel from Layer 6 first,
collect a week of real spend, then pick numbers based on the data.
Picking numbers cold would be guessing.

## Substitutes / partial implementations

These do *substrate-ish* work today but are not substitutes for the
real thing:

- **AlertManager** — fires, doesn't reason. The piece closest to
  observer-by-rule.

**Zulip Triager → Windmill → langgraph** (DM ingress) and the **ntfy
approval flow** (one-off HMAC-signed approvals) both existed here
before 2026-07-06 — both are gone along with `langgraph-agents`. There
is currently no substitute occupying either role.

When the substrate is built, each of these collapses into the
appropriate mode worker.

## What this is NOT

- Not a design doc. The shape above is HOMELAB-SPEC's, not a
  worked-through design for this cluster's specifics.
- Not a roadmap. There's no commit date.
- Not a list of substitutes that are good-enough. They aren't.

## See also

- HOMELAB-SPEC Layer 4 (Modes), Layer 5 (Task contract, Queueing,
  Blast radius, HITL SLA, Self-healing loop), Layer 6 (Routing and
  budget), Layer 7 (User surfaces).
- `.agents/instructions/data-classification.md`
- `.agents/skills/historian.md`
- `.agents/skills/upstream-watcher.md`
- memory `project_zulip_triager_webhook_done`
- memory `project_ntfy_pushover_migration_done`
