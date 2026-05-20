# Task Queue Substrate — design proposal

## Purpose

HOMELAB-SPEC Layer 5 requires a durable task queue with at-least-once
delivery, DLQ, visibility timeouts, and idempotency. `docs/src/orchestration_substrate.md` documents the gap and the
forward-looking task envelope. This doc proposes a specific substrate
and asks the operator to pick.

**Decision gate.** Phase 4 of the rollout plan cannot begin until this
choice is made. Phase 3 (idempotency, redaction, allowlist, OTel
propagation) lands the envelope-wired behaviors independently and is
unaffected by the substrate choice.

## Constraints

- **FOSS-only sourcing** (HOMELAB-SPEC Layer 3). No paid SaaS queues.
- **Network-local execution.** Substrate runs in-cluster.
- **No new deployments unless they earn it.** Three candidates below
  are all already deployed for other reasons; pick from those before
  adding a fourth.
- **At-least-once delivery** with visibility timeouts so a worker
  crash redelivers within minutes.
- **DLQ surface** that becomes Rob's review queue — failures land in
  Zulip / ntfy, not in silent log noise.
- **Trace propagation** — every operation should accept and forward an
  OTel `trace_id` (per Phase 2 OTel work).

## Candidates

### A — Windmill native queue

Status: **deployed** (`kubernetes/apps/home/windmill/`), CNPG-backed
job queue with web UI, retry config, and `wait_result` semantics
already used by the Triager webhook and approval flows.

**Pros**

- Already in production. Workflows for inbox routing, approval-post,
  awaiting-user-sweep, cost-cap-watcher, daily-digest run here today.
- Web UI: scheduled cron + ad-hoc runs + failure visualization.
- Built-in retry, timeout, parallelism controls per script.
- Persists via the `cnpg-windmill` Cluster (Garage-backed Barman) —
  durability is a property we already maintain.
- DLQ-ish behavior exists: failed flows surface in the UI with full
  context.

**Cons**

- Worker bodies are JS/TS/Python/Bash by Windmill convention; calling
  back into langgraph-agents' Python codebase happens via HTTP rather
  than direct call. That layer already exists (every workflow today
  POSTs to `/inbox` or `/admin/...`), but it's not zero-cost.
- Queue semantics are workflow-scoped, not task-envelope-shaped. To
  treat Windmill as the "task queue," we'd write a generic
  `task-router` workflow that takes a JSON envelope and dispatches.
- Trace-context propagation: needs us to thread `trace_id` through
  Windmill's HTTP-to-script boundary (doable; not free).
- Web UI is operator-facing; not a user-facing queue.

**Effort to operate as the substrate**

- Write a `task-router` Windmill flow that consumes the task envelope,
  dispatches to the appropriate mode worker (planner / executor /
  guardian / etc.), tracks retries, writes DLQ to a Zulip stream.
- Existing flows continue to exist; new flows for the modes get added.
- ~M effort: 1 generic router script + DLQ adapter + per-mode wiring.

### B — CNPG LISTEN/NOTIFY (Postgres pub/sub)

Status: **plumbing deployed** — `cnpg-langgraph-checkpoints` and
`cnpg-langgraph-memory` already exist; langgraph-agents already speaks
to Postgres via the AsyncPostgresSaver checkpointer.

**Pros**

- Native Python integration: psycopg async already in dependencies;
  `LISTEN ... NOTIFY` is a one-liner.
- Same durable substrate the graph already trusts (the checkpointer
  is Postgres-backed). Adding a task table next to checkpoints reuses
  the durability layer.
- Direct call: workers run inside `langgraph-agents` as asyncio tasks
  consuming the queue. Trace-context stays in-process.
- Cheap to model the envelope: one table, columns match the envelope
  spec. ULID for `id`, JSON column for the rest.
- DLQ is another table; failure path is one UPDATE.

**Cons**

- Build-it-yourself: we own the visibility-timeout logic, the
  redelivery scheduler, the retry-backoff loop, the worker pool
  management. Each of those is small but real.
- Operator UI: none out-of-the-box. We'd render the queue + DLQ via a
  Grafana dashboard or an `/admin/queue` endpoint.
- Single-Postgres failure couples graph state and queue state. If the
  CNPG cluster is down, both stop. (Already true for the graph; this
  doesn't add coupling, but doesn't remove it.)
- LISTEN/NOTIFY scales worse than purpose-built queues at higher
  message rates. At our scale (estimated <100 tasks/day), this isn't
  a real concern — but it's the textbook objection.

**Effort to operate as the substrate**

- One migration to add `tasks` + `task_dlq` tables.
- Worker loop in `agents/queue/` (new module): LISTEN, claim, run,
  ack/nack/dlq.
- Cron-fired sweeper that re-delivers tasks past visibility timeout.
- `/admin/queue` + Grafana dashboard.
- ~L effort: schema + worker + sweeper + DLQ surface + dashboard.

### C — Dragonfly streams

Status: **deployed** (`kubernetes/apps/databases/dragonfly/`,
v1.38.1, 3 replicas). Redis-protocol compatible; supports Redis
Streams.

**Pros**

- Redis Streams is the most queue-shaped of the three substrates.
  Native consumer groups, native pending-entries list (PEL) ≈ visibility
  timeout, native trim.
- Sub-millisecond latency for enqueue/claim. Easy on the cluster.
- 3 replicas, so the substrate itself is HA in a way the single-pod
  Windmill UI isn't.
- Python client (`redis-py`) is well-known.

**Cons**

- Adds a dependency on Dragonfly that langgraph-agents doesn't have
  today. (Easy: add to pyproject.)
- Persistence is RDB+AOF by default, lighter than Postgres durability.
  For a queue we care about, this is a *real* tradeoff — we'd want to
  verify Dragonfly's persistence config or accept "queue is best-effort
  durable, the source of truth is the graph state in CNPG."
- DLQ semantics are looser: streams have PEL for in-flight claims and
  XCLAIM for re-delivery, but DLQ is a separate stream we'd maintain.
- Operator UI: none.

**Effort to operate as the substrate**

- New `agents/queue/` module with Redis-streams consumer-group worker.
- DLQ-stream surface; cron sweeper.
- Persistence-config audit on Dragonfly (AOF? snapshot cadence?).
- ~M-L effort, less than CNPG-LN because Streams gives us most of the
  primitives.

## Comparison

| Concern | Windmill | CNPG LISTEN/NOTIFY | Dragonfly streams |
|---|---|---|---|
| Already deployed | ✓ | ✓ | ✓ |
| Native to langgraph-agents Python | ⚠ HTTP boundary | ✓ asyncio | ✓ redis-py |
| Operator UI out-of-the-box | ✓ | ✗ | ✗ |
| Durability | Barman + Garage | Barman + Garage | RDB+AOF (verify) |
| At-least-once / visibility timeout | DIY in flow | DIY | XPENDING / XCLAIM |
| Trace-context propagation | HTTP-thread it | in-process | in-process |
| Couples to graph state Postgres | no | yes | no |
| Operating cost-to-build | M | L | M-L |

## Decision

**Option B — CNPG LISTEN/NOTIFY.** Picked 2026-05-20.

## Recommendation

Rationale:

1. **In-process** matters more than "queue-shaped." The task queue
   exists to dispatch into the same langgraph-agents codebase. Direct
   asyncio call beats HTTP-thread-`trace_id`.
2. **Same durability tier as the graph itself.** Tasks and graph state
   live in the same CNPG cluster, backed up by the same Barman pipe to
   Garage. DR is one operation, not two with subtle skew.
3. **Coupling to CNPG is already there.** AsyncPostgresSaver pins the
   graph to Postgres anyway. Adding queue tables doesn't widen the
   blast radius.
4. **Dragonfly's lighter durability** is a real concern for a queue
   we want to trust. We could harden it, but that's work that doesn't
   gain us much over just using Postgres.
5. **Windmill's HTTP boundary** introduces re-serialization, breaks
   in-process tracing, and turns the task-envelope contract into "the
   shape of HTTP request bodies the dispatcher accepts." That's not
   what HOMELAB-SPEC means by a task envelope.

The CNPG-LN approach asks us to write the visibility-timeout loop
and DLQ surface ourselves. That's real work, but it's also work we
end up owning regardless of substrate (Windmill workflows need DLQ
shape too; Dragonfly needs PEL sweeping). Building it on the
substrate that already owns our durability is the cleanest version.

## What the build-out looks like (Phase 4 preview)

If you approve Option B, Phase 4 ships:

1. Migration on `cnpg-langgraph-checkpoints` (or a new
   `cnpg-langgraph-queue`) adding `tasks` + `task_dlq` tables. ULID
   primary key, JSONB column for the envelope, status enum, attempt
   counter, claimed_at / claimed_by timestamps, ttl_expires_at.
2. New module `src/agents/queue/`:
   - `enqueue(envelope) -> task_id`
   - Worker loop using `LISTEN` + a poll fallback every N seconds for
     visibility-timeout reclaim.
   - DLQ writer on terminal failure.
3. `/inbox` switches from synchronous-call-into-graph to `enqueue` +
   immediate-202. Old behavior gated behind a `?sync=true` flag for
   the duration of the migration.
4. Worker is a separate Deployment (sibling to the langgraph-agents
   API pod) so request-handling and dispatch don't share an event
   loop.
5. `/admin/queue` endpoint + a Grafana dashboard showing queue depth,
   per-status counts, DLQ entries, claim latency.
6. DLQ → Zulip `#dlq` stream; per-failure post with task_id, envelope
   summary, last error, "retry" / "abandon" actions.

Per `.agents/skills/pre-submit.md`, this is large enough to be a
sweep PR or split into 3-4 small PRs:

- Migration + enqueue/dequeue primitives
- Worker pool + visibility-timeout sweeper
- `/inbox` cutover + back-compat flag
- DLQ surface + dashboard

## Next steps

Phase 4 build-out per the preview above. Schema for the new tables
lives on `cnpg-langgraph-checkpoints` (re-use existing Cluster) or a
new `cnpg-langgraph-queue` Cluster — to be decided when migration
PR is drafted.

## See also

- `docs/src/orchestration_substrate.md` — the gap doc; this is the
  decision doc
- HOMELAB-SPEC Layer 5 (Task contract, Queueing, Self-healing loop)
- `.agents/instructions/data-classification.md` — envelopes carry
  `data_tier`; whatever substrate is picked must respect tier when
  emitting summaries / vault writes
- Memory `[[reference_pyspy_in_readonly_python_pod]]` — adjacent
  langgraph-agents in-pod debugging pattern
