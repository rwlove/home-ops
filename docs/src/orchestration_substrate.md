# Orchestration substrate

## Status

**Not built as of 2026-05-20.** HOMELAB-SPEC Layer 5 references a task
contract, queue substrate, DLQ, and per-mode workers that this cluster
does not yet implement. This document names the gap, describes what
exists today, and sketches the forward-looking shape so we don't lose
the specs while we wait.

## What exists today

The closest things to a task pipeline in this cluster:

- **AlertManager → HolmesGPT** for alert triage. AlertManager fires →
  HolmesGPT investigates → result posts to Zulip / Pushover.
- **Zulip Triager bot → Windmill → langgraph-agents `/inbox`** for
  DM-shaped intents. The Triager outgoing-webhook converts DMs to
  HTTP and Windmill brokers to langgraph. See memory entry
  `project_zulip_triager_webhook_done`.
- **ntfy → langgraph approval endpoint** for tap-to-approve actions
  on Android. HMAC-signed, single-use. See memory entry
  `project_ntfy_pushover_migration_done`.
- **HA voice + ntfy notifications** for Renee-facing surfaces.
  Currently end-user-driven, not agent-mediated.

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
on the queue substrate. Until the substrate lands, observer
responsibilities live in AlertManager + HolmesGPT, and guardian
responsibilities live in Rob (the human) responding to ntfy /
Pushover approval requests.

## Token / cost budget (deferred)

HOMELAB-SPEC Layer 6 asks for a daily + weekly MAX-phase budget. Not
set today. Plan: ship the Grafana dashboard panel from Layer 6 first,
collect a week of real spend, then pick numbers based on the data.
Picking numbers cold would be guessing.

## Substitutes / partial implementations

These do *substrate-ish* work today but are not substitutes for the
real thing:

- **HolmesGPT** — single-task investigation per alert. No queue, no
  envelope, no idempotency, no trace correlation.
- **Zulip Triager → Windmill → langgraph** — DM ingress only. No DLQ,
  no retry policy, no priority routing.
- **ntfy approval flow** — one-off HMAC-signed approvals. Not a
  generic guardian.
- **AlertManager** — fires, doesn't reason. The piece closest to
  observer-by-rule.

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
