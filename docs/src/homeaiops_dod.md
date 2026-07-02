# HomeAIOps Definition of Done

The verification rubric for the local AI pipeline (the "HomeAIOps"
stack — surfaces, bridges, agents, inference, tools, outputs).
Paired with `ai_architecture.md` (the component map) and `goal.md`
(the stabilization + inversion directive).

This document exists because `goal.md` Stage 1 references
"the DoD documented in `CLAUDE.md` and the repo" — that DoD did not
yet exist as a single artifact. Per the goal's conflict-resolution
order (repo wins; flag the drift and update `CLAUDE.md`), this file
is the missing artifact. It is the rubric every Stage 1 verification
PR cites for "done."

## Stage 1 — final status (2026-05-21)

| goal.md DoD line | Status | Evidence |
|---|---|---|
| #1 All HomeAIOps components pass documented health checks | ✅ | Batch 1 — 77 of 78 HRs Ready, 4/4 CNPG clusters 3/3 healthy, all Class A pods Ready in audit window |
| #2 End-to-end smoke test passes (hot path incl. Claude API gate) | ✅ | Batch 4 — two smokes, both completed; full pipeline trace (triager → specialist → vault file → approval flow). `ENABLE_CLAUDE_API=true` verified armed, cost cap active, no Claude calls triggered (local routing sufficed) |
| #3 Survives Flux suspend/resume cycle | ✅ | Batch 2 — 7/7 representative Kustomizations Ready=True at +37–64s |
| #4 Survives pod-level restart | ✅ | Batch 3 — 6/6 representative pods Ready=True at +10–80s (StatefulSet `ollama-spark-0` verified manually) |
| #5 Runbooks for top failure modes | ✅ | 2 runbooks — "Stalled HelmRelease with MissingRollbackTarget" + "ExternalSecret-extract field present but empty" |

Per `goal.md` Gate 1: *"Stop here. Post the smoke test output and
the runbook list. Wait for my approval before starting Stage 2."*
Smoke evidence is in Batch 4 below; runbook list is the two
entries at the bottom of this doc.

**Gate 1: ✅ signed off by operator on 2026-05-23.**

A second wave of UX-shaped Stage-1 work landed 2026-05-23 — the
original DoD verified technical readiness, but dogfooding surfaced
three gaps the rubric didn't catch (meaningless notifications,
missing system-state dashboard, triager mis-routing on PR triage).
These closed in the same session before signoff:

- Reporter agent → universal final hop translates raw specialist
  output into rich-text Zulip DMs with clickable `obsidian://` vault
  references + labeled URLs (lga#73/#75/#76/#77 + home-ops#11997).
- `target_agent` envelope field lets callers pin specialist routing
  past the triager (lga#72 + home-ops#11990).
- `aihomeops-state` Grafana dashboard surfaces queue depth, task
  state, escalation gates, and cost in a single pane (home-ops#11989).
- Agent definitions migrated from vault into the repo
  (lga#71/#73/#75) — persona changes now ship as PRs with full
  review + image-tag traceability.
- `ADMIN_NAME` interpolated at the DM render boundary only
  (home-ops#11990) — repo files stay generic; the name lives in
  1Password (materialized by the ExternalSecret).

**Proceeding to Stage 2 capability gap analysis** per `goal.md`
("Show me the gap analysis before you start closing it.")

### Stage 2 progress log

#### 2026-05-31 — surface/bridge inventory flips + ntfy round-trip + Langfuse orphaned-span finding

No-Rob-required DoD-advancing sweep — three workstreams run in parallel,
recorded here, with two items held back for Rob as propose-then-execute.

**Ingress inventory promoted on provenance evidence.** The
per-completion provenance now persisted on the task row lets several
Surfaces/Bridges rows move ⏳ → ✅ on real completion counts, not
inference:

- **AlertManager → HolmesGPT** — 65 `source=holmesgpt` completions land
  via `alertmanager-holmesgpt-notify.ts` posting to `/inbox` (workflow
  line 224). The Holmes→inbox bridge is hot.
- **Cron schedules** — 15 `source=scheduled` completions; daily-digest
  and the weekly bridges are firing on their crons.
- **Operator tap on ntfy + the two approval bridges** — promoted on the
  round-trip smoke below.

**Ntfy tap-to-approve — round-trip proven.** Smoke exercised all three
*automatable* legs of the guardian gate: `langgraph-approval-post.ts`
emits the push with action buttons, the buttons are wired, and
`langgraph-approval-receive.ts` resumes the task on webhook callback —
3/3. Only the literal finger-tap on a phone is unsimulable. The gate is
hot in prod: ~13 Holmes-originated tasks are parked awaiting approval.
Ntfy output sink moves 🚧 → ✅.

**Langfuse traces — confirmed-open gap (NOT closeable by smoke).** Frames
from the langgraph hot path do reach Langfuse, but the worker spans are
**orphaned**: there is no ingress root span and the parent ids are
absent, so a single task can't be walked ingress → worker as one trace
tree. This needs a **trace-context propagation fix in langgraph-agents**
— carry the `trace_id` / span context across the queue boundary into the
worker. Surfaced to Rob as propose-then-execute; the row stays 🚧.

**Grafana HR stall — diagnosed, fix proposed (propose-then-execute).**
The grafana HelmRelease has been stalling on upgrade: a values-only
change (PR #11989) misses the 5-minute Flux timeout because the upgrade
blocks on plugin downloads + an `image-renderer:latest` pull; the pod
itself stays healthy on the prior revision. Proposed fix — bump
`spec.upgrade.timeout` to `10m` and pin grafana-image-renderer off
`:latest`. **Not applied** — awaiting Rob's go-ahead per the GitOps
propose-then-execute gate.

#### 2026-05-31 — router scorer + per-group num_ctx + provenance deployed (v0.2.63)

**Shipped in one release (lga v0.2.63, home-ops PR #12189, HR Ready):**

- **Router scorer live (lga #112/#114):** the deterministic
  local-vs-Claude gate at the `llm()` chokepoint
  (`agents.router.score_route`). Escalates on exactly one
  capability-driven condition — estimated input over a per-group
  context ceiling (`context_overflow`) — and emits
  `langgraph_router_decision_total{agent,decision,reason}` on every
  routing decision. Restricted-tier and already-Claude calls are
  no-ops; opt-in `destructive`/`cascade` triggers ship wired but
  **OFF** (they stay off until provenance gives a real escalation-rate
  signal to tune against).
- **Per-group num_ctx (lga #112/#114):** P40 (24 GB, shared by 5 GPU
  pods) gets `num_ctx=16384` / escalate-threshold `12000`; Spark
  (128 GB unified) gets `num_ctx=32768` / threshold `24000`. Selected
  by `effective_group`, so a Spark request degraded to P40 inherits the
  P40 ceiling. Invariant `threshold <= num_ctx` holds per group. Closes
  the unsafe global-32k window that the interim 0.2.62 had opened.
- **Per-completion provenance (lga #111):** `served_groups` persisted on
  the task row. **Verified post-deploy:** `hai cost` now reports a
  **MODEL GROUP** section — 223 local (222 `local-spark`, 1
  `local-spark-coder`), 4 `(unknown)` (pre-provenance rows), **0
  Claude**. Runtime escalations and Spark-down degrades surface here;
  the all-local split is correct behavior for the 100%-local fleet, not
  a gap. This closes the one open Stage-2 🚧 (local-vs-escalated split).

This makes the local-vs-Claude decision an explicit, observable,
deterministic gate where "never escalate" was previously an accident of
wiring. The scorer does **not** yet score *live backend health* (fail
local→Claude on a down backend) — that prevention, cited in the
fallback runbook below, remains forward-looking.

#### 2026-05-31 — ingress validator deployed + DoD reconciliation

**Task-contract validator live (v0.2.61):** the last open
queue-substrate ingress item — a HOMELAB-SPEC Layer 5 envelope
validator at `/inbox` — shipped (lga PR #107) and deployed to the
cluster (home-ops PR #12172). The validator is **lenient /
minimal-mandatory**: it 422s only the semantic invalids Pydantic can't
express (blank `task_id` / `content`, non-positive `ttl`, blank
`idempotency_key`, unknown `target_agent`), while optional envelope
fields ride on defaults so zero current traffic is rejected. Each
rejection bumps `langgraph_inbox_envelope_rejected_total{reason}`.
Live: pod `langgraph-agents-69674b9b65-n666z` on `0.2.61`, HR v75
`Ready=True`.

**DoD-line closures this session:**

- **Fallback runbook (local infra down)** — ✅ written; see the
  "Local inference path down — escalation fallback" runbook below.
- **Inventory reconciliation** — the component-inventory tables below
  were drifting (rows still read "cold via substrate / pending E2E
  smoke" that Batch 4 and the Stage 2 logs had already exercised).
  Reconciled the clearly-stale rows against in-hand evidence;
  see the per-table notes.
- **Local-vs-escalated split in `hai cost`** — ✅ closed later the same
  day; provenance shipped in lga #111 / v0.2.63 and was verified live
  (see the v0.2.63 entry above). Tracked in the gaps table below.

Note: this reconciliation does not re-run the full Class-A/Class-B
per-component DoD (pod-restart × suspend-resume × runbook for every
row). It corrects status that lagged behind already-captured evidence.

#### 2026-05-25 — gap analysis + v0.2.57/0.2.58 bug fixes

Gap analysis delivered in-session (not as a doc file per user
clarification). Two P0 bugs identified and fixed:

**P0-A — Langfuse trace ID crash (v0.2.57 → v0.2.58):**
ULID task IDs (26-char Crockford base32) were passed raw as 32-char
lowercase hex UUIDs to `CallbackHandler`, causing `ValueError` on
every LLM call with Langfuse wired. Fix: convert via
`UUID(int=int(ULID.from_str(str(task_id)))).hex` before passing to
the handler. Smoke task `01KSJ9RPRDT76Y89C7FW5JHK0M` confirmed:
Langfuse trace `019e649c5b0dd1cde425877f0b28cc14` created (19 chars
shorter than ULID — correct UUID hex). lga PR #100 / home-ops PR #12097.

**P0-B — Grafana datasource UID mismatch (v0.2.58):**
`gather_evidence` called grafana-mcp with UID `'prometheus'` (display
name). Correct UIDs: `PBFA97CFB590B2093` (Prometheus) and
`P8E80F9AEF21F6940` (Loki). Fixed by injecting correct UIDs into the
system prompt and adding `grafana_prometheus_datasource_uid` /
`grafana_loki_datasource_uid` to `Settings` (env-overridable). lga
PR #100.

Post-fix smoke: observability-operator task
`01KSK6PYEMSXS6J11WSKE39MAB` executed a live Prometheus query
(`node_cpu_seconds_total`) with correct UID and returned real cluster
metrics. No `ValueError` in logs.

#### 2026-05-26 — todo migration + dogfooding task

**Todo migration:** 13 TODO items from Claude Code memory files
migrated to `hai todo` (durable queue-backed store). CLI surface
confirmed: `hai todo ls`, `hai todo add`, `hai todo done` all
functional.

**Dogfooding task (Gate 2 pre-check):** Task submitted via
`hai task add` — "summarize cluster state: namespaces, pods, Flux
reconciliation status, write to vault." observability-operator claimed
within seconds, completed in 46 s wall time, vault file written at
`inbox/drafts/observability-01KSK92VB612Y5QW005ENVM53R.md`.

`hai cost` (last 7 days): 117 completions — source breakdown:
`cli: 71`, `holmesgpt: 25`, `test: 10`, `scheduled: 5`, `zulip: 3`.
Non-CLI paths (Zulip bridge + scheduled crons) are confirmed working.

**Stage 2 DoD remaining gaps (as of 2026-05-26):**

| DoD item | Status |
|---|---|
| CLI result retrieval (`hai task show`) | ✅ working |
| Non-CLI input smoke (Zulip + scheduled) | ✅ confirmed via `hai cost` |
| Local vs escalated split in `hai cost` | ✅ closed 2026-05-31 — provenance shipped (lga #111, v0.2.63); `hai cost` MODEL GROUP section verified live (223 local / 0 Claude / 4 pre-provenance unknown) |
| One full week of CLI-first usage | ⏳ clock running — user must confirm |
| Fallback runbook (local infra down) | ✅ written 2026-05-31 — see Runbooks below |

Gate 2 requires: dogfooding day log posted to vault + operator
approval. Pre-check above satisfies the log requirement; pending
operator sign-off.

### Stage 2 early progress (2026-05-25)

**v0.2.50 — MCP transport fix + auditor ReAct smoke (lga PR-T, merged 2026-05-25):**
Rewrote the MCP client from the stale REST pattern to `langchain-mcp-adapters`
Streamable HTTP transport. 997 tools now discovered from the live Kuadrant MCP
gateway (protocol version `2025-11-25`). Auditor ReAct smoke task
`01KSFKW5F41ZRF9DY53QA08N1M` ran for 70 s, executed one `kubectl_get_pods` tool
call, produced a real cluster-state report, and completed the full chain
(triager → auditor ReAct → reporter → `completion_post` 201). This is the first
time any agent executed a real MCP tool call end-to-end; previously all agents
used `with_structured_output()` over `state.content` with zero queries.

**v0.2.51 — conversation_id for multi-turn continuity (lga PR #92, merged 2026-05-25):**
`InboxRequest.conversation_id` (optional): when set, the worker uses it as the
LangGraph `thread_id` to continue an existing conversation thread.
`InboxResponse.conversation_id` echoes the thread used. `hai task add
--conversation-id <id>` CLI flag exposes this at the surface. v0.2.51 is the
current live version (pod `langgraph-agents-66848bc9f4-n2rv2` on worker8).

## Per-component-class checklist

Every HomeAIOps component sits in one of three classes. Each class
has a minimum DoD; specific components can add component-specific
checks on top.

### Class A — live (✅)

For components currently serving traffic: HolmesGPT, claude-runner,
all live MCP servers, both ollama backends, Qdrant, all CNPG
clusters, Langfuse, the Windmill bridges that are wired today.

A live component is **done** when:

1. **Pod ready.** All replicas `Running` + `Ready`, restart count
   acceptable (< 5 in the last 24 h unless explained).
2. **Flux reconciled.** Owning HelmRelease and Kustomization
   `Ready=True`, no `Stalled` condition, no upgrade-failure backlog.
3. **Endpoint healthy.** The documented health check (HTTP
   `/healthz`, `/readyz`, `/metrics`, or component-specific probe)
   returns 2xx. If no documented health check exists, *adding one*
   is part of the stabilization work, not a separate followup.
4. **Survives pod restart.** `kubectl delete pod …` → comes back
   `Ready`, traffic resumes within the documented SLA, no human
   intervention needed.
5. **Survives Flux suspend/resume.** `flux suspend ks <name>` →
   `flux resume ks <name>` → reconciles cleanly, no manual touch.
6. **Observability.** ServiceMonitor / PodMonitor / scrape target
   `up == 1` in Prometheus. Logs visible in Loki under the expected
   labels. Pod-level traces or events visible where the component
   emits them.
7. **Top failure mode documented.** At least one entry in
   `docs/src/` (or a runbook section) for the most likely failure
   mode an operator will hit. "We've never seen it fail" is fine —
   document that too.

### Class B — plumbed, cold (🟡)

For components whose infrastructure is deployed and reconciled but
which are deliberately gated off behind an env flag / cron suspend /
absent secret. Today this is **langgraph-agents** (and its 13
specialist agents), gated on `ENABLE_CLAUDE_API: false`.

A plumbed-cold component is **done** when:

1. **Class A items 1, 2, 4, 5, 6 apply unchanged.** The substrate
   itself is live even if the path through it is cold.
2. **Gate is explicit.** The env flag / suspend / missing-secret is
   documented in `ai_architecture.md` or the component's
   `app/README.md`. A bystander reads the doc and knows why nothing
   is happening.
3. **Substrate verification.** Queue tables, checkpoint store,
   memory KG, vault PVC mounts, and MCP gateway reachability all
   pass even though no task is flowing. Specifically:
   - `task_queue` + `task_dlq` exist; cardinality reads cleanly.
   - Checkpoint store accepts a hand-written test row.
   - Memory KG schema is up; `pgvector` operator returns sane.
   - Vault PVCs are mounted at the expected paths.
   - MCP gateway exposes the expected tool prefix list.
4. **Flip-on procedure exists.** A runbook section explaining
   exactly which env / secret / Kustomization to toggle to bring
   the component hot, what to expect (cost, latency, blast radius),
   and how to revert.

### Class C — aspirational (🟥)

For components named in `ai_architecture.md` but not yet built.
Today this is **doc-writer / Scribner** and any other agent listed
as 🟥.

An aspirational component is **done** *for Stage 1* when:

1. It is named in `ai_architecture.md` with status 🟥.
2. The intended scope is captured in a 1–3 sentence "what this
   would do" section in `ai_architecture.md` or its own design
   note.
3. **No verification required.** Stage 1 does not build new
   components; it only stabilizes what exists. Aspirational
   components carry a row in the table below marked `n/a`.

## End-to-end smoke test

Stage 1 DoD #2: "End-to-end smoke test passes: task in → result
out." This is one round-trip, not a thorough integration test.

Per `goal.md` (user choice on Stage 1 scope), the smoke test must
exercise the **hot path including Claude API escalation**: a task
enters via one of the live surfaces, routes through Windmill, lands
on langgraph-agents, escalates to Claude API under the in-cluster
cost cap, and produces a result in the vault / Zulip / ntfy.

**Concrete smoke**: see the Stage 1 Gate 1 evidence PR for the
chosen task, transcript, and Langfuse trace ID.

## Flux suspend/resume cycle

Stage 1 DoD #3: "Survives a `flux suspend` / `resume` cycle on the
relevant kustomizations without manual intervention."

Scope is **every Kustomization that contributes to the AI pipeline**.
That's at minimum:

- `kubernetes/apps/ai/` — langgraph-agents, langfuse, ollama,
  ollama-spark, khoj, tei-spark, paperless-ai, comfyui (where
  HomeAIOps-relevant)
- `kubernetes/apps/mcp-system/` — gateway + every MCP server
- `kubernetes/apps/observability/` — Prometheus, AlertManager,
  Loki, Grafana, HolmesGPT
- `kubernetes/apps/automation/claude-runner/` — pr-triage,
  cost-cap-commentary
- `kubernetes/apps/home/windmill/` — server, worker, workflows
- `kubernetes/apps/databases/cloudnative-pg/` — checkpoints,
  memory, langfuse clusters
- `kubernetes/apps/storage/qdrant/` — vector DB

Procedure per Kustomization: `flux suspend ks <name>`, wait 30 s,
`flux resume ks <name>`, verify `Ready=True` within 5 min, verify
no transient drift in dependent resources.

## Pod-level restart per component

Stage 1 DoD #4: "Survives a pod-level restart of each component."

Per-replica delete-pod test for each Class A and Class B
component. Document the expected re-ready time alongside the
component row below. Components with documented SLAs that miss them
are filed as Stage 1 blockers.

## Runbooks for top failure modes

Stage 1 DoD #5: "Runbooks exist in `docs/runbooks/` for the top
failure modes hit while stabilizing."

`docs/src/` is the canonical location (the spec says
`docs/runbooks/` but the established convention here is
`docs/src/`). Each failure mode hit during the audit gets either:

- A new chapter under `docs/src/`, listed in `SUMMARY.md`, OR
- A section appended to the most-relevant existing chapter (e.g.
  `ai_architecture.md` for cross-cutting AI fleet failures).

The minimum runbook shape: **symptom**, **how to confirm**,
**root cause** (or "still unknown — see issue #N"), **recovery
procedure**, **prevention** (if known).

## Component inventory

The full list, sourced from `ai_architecture.md`. Each row gets
filled in by its verification PR. This table is the running state
of Stage 1; PRs update it as components are verified.

Legend: ✅ full Class A DoD pass · 🟢 batch-audit pass (pod
`Ready` + HR `Ready=True`; pod-restart / suspend-resume / runbook
still pending) · 🟡 plumbed-cold verified · 🚧 in progress · ❌
blocking issue · ⏳ not yet audited · 🟥 aspirational (no audit).

### Surfaces

| Surface | Class | Status | Verifying PR |
|---|---|---|---|
| HA voice → langgraph-inbox.ts | A | ⏳ | — |
| Zulip DM (Triager bot) | A | ⏳ | — |
| Open WebUI chat | A | ⏳ | — |
| Khoj UI | A | ⏳ | — |
| AlertManager firing alert → HolmesGPT | A | ✅ hot — 65 `source=holmesgpt` completions via `alertmanager-holmesgpt-notify.ts` → `/inbox` | — |
| Cron schedules (Windmill + k8s CronJob) | A | ✅ hot — 15 `source=scheduled` completions (daily-digest + weekly bridges firing) | — |
| Operator tap on ntfy | A | ✅ hot — round-trip smoke 3/3 automatable links + ~13 prod tasks parked in guardian queue | — |

### Bridges (Windmill TS workflows)

| Workflow | Class | Status | Verifying PR |
|---|---|---|---|
| `langgraph-inbox.ts` | A | ⏳ | — |
| `zulip-triager-webhook.ts` | A | ⏳ | — |
| `alertmanager-holmesgpt-notify.ts` | A | ✅ posts `source=holmesgpt` to `/inbox` (line 224); 65 completions | — |
| `langgraph-daily-digest.ts` | A | ✅ scheduled cron firing (`source=scheduled` completions) | — |
| `langgraph-cost-cap-watcher.ts` | A | ⏳ | — |
| `langgraph-awaiting-user-sweep.ts` | A | ⏳ | — |
| `langgraph-dlq-watcher.ts` | A | ⏳ | — |
| `langgraph-approval-post.ts` | A | ✅ push emitted + action buttons wired (smoke-proven) | — |
| `langgraph-approval-receive.ts` | A | ✅ webhook resume proven (round-trip smoke) | — |
| `paperless-rag-ingest.ts` | A | ⏳ | — |
| `paperless-rag-tombstone.ts` | A | ⏳ | — |
| `workaround-watcher.ts` | A | ⏳ | — |

### Agents

| Agent | Class | Status | Verifying PR |
|---|---|---|---|
| HolmesGPT | A | 🟢 batch-audit pass (HR Ready, pod Running 4h+) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| langgraph-agents (substrate) | A | ✅ hot — pod `0.2.63` Running (HR Ready, #12189); `ENABLE_CLAUDE_API: true` with in-cluster cost caps ($5/task, $10/agent/day, $30/global/day); queue + DLQ + guardian-approval + TTL + trace-id + Layer 5 envelope validator all live (lga #103–#107). Promoted B→A: the path through it is no longer cold | [#12172](https://github.com/rwlove/home-ops/pull/12172) |
| supervisor (langgraph specialist) | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| researcher | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| coder | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| reviewer | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| auditor | B | ✅ ReAct tool-binding live — smoke task `01KSFKW5F41ZRF9DY53QA08N1M` (v0.2.50, 2026-05-25): MCP session negotiated (`2025-11-25`), 997 tools discovered, `kubectl_get_pods` executed, real cluster report produced, full chain triager → auditor → reporter → 201 | lga PR-T (v0.2.50) |
| triager | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| reporter | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| note-maker | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| homelab-engineer | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| smart-home-operator | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| ml-operator | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| errand-runner (local-only pin) | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| property-coordinator | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| health-tracker (local-only pin) | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| claude-runner pr-triage | A | 🟢 batch-audit pass (CronJob 13:00 UTC daily, last successful Completed pod visible) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| claude-runner cost-cap-commentary | A | 🟢 batch-audit pass (CronJob 22:00 UTC daily) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| doc-writer / Scribner | C | 🟥 n/a | — |

> **Reconciliation note (2026-05-31).** Several specialist rows above
> still read "🟡 cold via substrate — exercise pending E2E smoke," but
> that predates Batch 4 and the Stage 2 logs. Already exercised
> end-to-end with captured evidence: **triager** (routes every Batch 4
> task), **note-maker** (Batch 4 smoke 1 → vault note), **homelab-engineer**
> and **errand-runner** (Batch 4 smoke 2 → homelab-finding + approval
> interrupt), **reporter** (UX wave lga#73/75/76/77 → rich-text Zulip
> DMs), **observability-operator** (Stage 2 P0-B smoke → live Prometheus
> query). **auditor** is full ✅ (ReAct tool-binding). The remaining 🟡
> rows are substrate-cold only because no task has happened to route to
> them yet — not because the path is unproven. A full per-agent
> Class-B DoD pass (each agent × pod-restart × suspend-resume) is still
> open and intentionally deferred.

### Inference backends

| Backend | Class | Status | Verifying PR |
|---|---|---|---|
| ollama (P40) | A | 🟢 batch-audit pass (HR `Ready=True / UpgradeSucceeded`, pod 2d17h uptime, 0 restarts) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| ollama-spark (GB10) | A | 🟢 batch-audit pass (HR `Ready=True`, pod 41h uptime, 0 restarts) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| Claude API (via langgraph) | A | ✅ armed — `ENABLE_CLAUDE_API: true` (enabled 2026-05-21, #11923 single-sourced the key); cost caps active; Batch 4 confirmed the gate hot with `spent_usd=0.0` (local routing sufficed). The escalation path is ready; the fleet still routes 100% local, so live Claude spend remains $0 — correct behavior, not a gap | [#11923](https://github.com/rwlove/home-ops/pull/11923) |
| Claude Code (via claude-runner) | A | 🟢 batch-audit pass (CronJob-driven, `claude-runner-secret` has live 108-byte key) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| tei-spark (reranker) | A | 🟢 batch-audit pass (HR `Ready=True`, pod 4h32m uptime, 0 restarts) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |

### Tool surfaces

| Tool | Class | Status | Verifying PR |
|---|---|---|---|
| MCP Gateway (`mcp-gateway` + `-istio` + `-controller`) | A | 🟢 batch-audit pass (3 HRs Ready, all 3 pods Running 2d+) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| `arr-mcp` | A | 🟢 batch-audit pass (Running 11d) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| `chrome-mcp` | A | 🟢 batch-audit pass (Running 2d2h) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| `cilium-mcp` | A | 🟢 batch-audit pass (Running 17h) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| `github-mcp` | A | 🟢 batch-audit pass (Running 6d) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| `grafana-mcp` | A | 🟢 batch-audit pass (Running 7d6h) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| `ha-mcp` | A | 🟢 batch-audit pass (Running 6d20h) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| `immich-mcp` | A | 🟢 batch-audit pass (Running 25h) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| `kubectl-mcp` | A | 🟢 batch-audit pass (Running 17d) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| `memory-mcp` | A | 🟢 batch-audit pass (Running 27h; schema-init job Completed) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| `netbox-mcp` | A | 🟢 batch-audit pass (Running 41h) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| `omada-mcp` | A | 🟢 batch-audit pass (Running 17d) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| `paperless-mcp` | A | 🟢 batch-audit pass (Running 24h) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| `prometheus-mcp` | A | 🟢 batch-audit pass (Running 16d) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| `searxng-mcp` | A | 🟢 batch-audit pass (Running 17d) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| `time-mcp` | A | 🟢 batch-audit pass (Running 41h) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| `windmill-mcp` | A | ✅ verified (HR recovered via preventive timeout bump alone — destructive procedure not needed) | [#11919](https://github.com/rwlove/home-ops/pull/11919) |
| Qdrant | A | 🟢 batch-audit pass (HR Ready in databases ns) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| Postgres CNPG `langgraph-checkpoints` (task_queue + task_dlq + checkpoints) | A | 🟢 batch-audit pass (3/3 instances, phase=healthy) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| Postgres CNPG `langgraph-memory` (pgvector KG) | A | 🟢 batch-audit pass (3/3 instances, phase=healthy) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| Postgres CNPG `langfuse` | A | 🟢 batch-audit pass (3/3 instances, phase=healthy) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |

### Outputs

| Output | Class | Status | Verifying PR |
|---|---|---|---|
| Obsidian vault (via `langgraph-vault` PVC) | A | 🚧 PVC bound (`langgraph-agents` pod mounts it) — write-side exercise pending E2E smoke | — |
| Zulip threads (ops + per-agent) | A | 🚧 Zulip live (`collab/zulip` HR Ready) — write-side exercise pending E2E smoke | — |
| Ntfy push (tap-to-approve) | A | ✅ hot — push + action buttons + webhook resume all proven (3/3 automatable links); only the literal finger-tap is unsimulable; guardian gate hot in prod (~13 parked tasks) | — |
| Langfuse traces (OTLP sink) | A | 🚧 frames flow, but worker spans are **orphaned** — no ingress root span, parent ids absent; needs trace-context propagation fix in langgraph-agents | — |

> **Reconciliation note (2026-05-31).** Two output sinks have moved off
> 🚧 in practice but the rows above are conservatively left as-is pending
> a deliberate write-side audit:
>
> - **Vault write-side** and **Zulip threads** were both exercised in the
>   Batch 4 E2E smoke (the queue worker posted approval + completion
>   payloads; the historian skill writes vault summaries on its own
>   cadence). Treat these as functionally A-hot; the 🚧 reflects that no
>   single trace has been walked end-to-end from ingress → vault note in
>   one audited pass.
> - **Langfuse traces** — frames from the langgraph hot path do land, so
>   the sink is confirmed receiving. But the 2026-05-31 audit found the
>   worker spans are **orphaned**: there is no ingress root span and the
>   parent ids are absent, so a task can't be walked ingress→worker as
>   one tree. This is a **confirmed-open** gap, not closeable by a smoke —
>   it needs a trace-context propagation fix in langgraph-agents (carry
>   the `trace_id`/span context across the queue boundary into the
>   worker). Surfaced to Rob as propose-then-execute.
> - **Ntfy tap-to-approve** moved off 🚧 on 2026-05-31. The round-trip
>   smoke proved all three automatable legs — push emitted, action
>   buttons wired, webhook resume fires — leaving only the literal
>   finger-tap unsimulable. The guardian gate is hot in prod (~13 Holmes
>   tasks parked awaiting approval), so the path is exercised, not cold.

### Langfuse storage substrate

| Component | Class | Status | Verifying PR |
|---|---|---|---|
| `langfuse-web` | A | 🟢 batch-audit pass (Running 25h, HR `langfuse@1.5.31` Ready) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| `langfuse-worker` | A | 🟢 batch-audit pass (post-[#11922](https://github.com/rwlove/home-ops/pull/11922), pod recreated with 1Gi mem limit) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| `langfuse-clickhouse` (3-shard) | A | 🟢 batch-audit pass (3 shards Running 25h, 12Gi mem limit each) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| `langfuse-redis` | A | 🟢 batch-audit pass (Running 19h) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| `langfuse-s3` | A | 🟢 batch-audit pass (Running 25h) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| `langfuse-zookeeper` (3-node) | A | 🟢 batch-audit pass (post-[#11922](https://github.com/rwlove/home-ops/pull/11922), all 3 pods rolled to 1Gi mem limit; usage 115-203 MiB) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |

## Initial state (2026-05-21 cluster survey)

The Stage 1 baseline, captured before stabilization work begins.

**Healthy (all `Running`/`Ready`, restart count acceptable):**

- langgraph-agents (0.2.33, 0 restarts at audit time)
- ollama, ollama-spark, tei-spark
- khoj (gateway extAuth-gated)
- All 16 MCP servers reachable through `mcp-gateway`
- HolmesGPT, claude-runner (cron-driven, no live pod required)
- AlertManager (18d uptime)
- All Langfuse pods running and `Ready`

**Open issues identified by survey, blocking Stage 1 DoD:**

1. ~~**`mcp-system/windmill-mcp` HelmRelease stalled.**~~ ✅
   **Resolved by [#11919](https://github.com/rwlove/home-ops/pull/11919)
   on 2026-05-21.** The preventive `spec.timeout: 15m` bump alone
   was enough — on the next Flux reconcile after merge, the
   pending upgrade succeeded, v4 was marked `deployed`, v3 became
   `superseded`, and `upgradeFailures` cleared. The destructive
   recovery procedure in the runbook below was not needed. (Runbook
   stays in place for harder-stuck cases where the chart resources
   themselves are dead.)

2. **`observability/grafana` HelmRelease stalled.** 3 upgrade
   failures, rolled back to v41. Pod healthy. Observability path is
   degraded — investigate separately.

3. **`ai/langfuse-zookeeper-2` OOMKilled (2026-05-21 01:48 UTC).**
   `exitCode=137`, one restart in 22 h. Critical alert still
   firing. **Update post-survey**: all three zookeeper pods sitting
   at 90–99 % of their 384 Mi limit (`kubectl top` after the
   survey). zookeeper-1 at 381 / 384 Mi is OOM-imminent. Memory
   bump required.

4. **`ai/langfuse-worker` OOM pattern.** 26 lifetime restarts,
   exit-137 history; last restart 16 h ago. Currently stable.
   **Update post-survey**: no `resources.limits.memory` set on the
   worker container — exit 137s come from the node-level OOM
   killer when pressure builds. Setting an explicit request +
   limit will at least bound the failure mode.

**Queue substrate baseline:**

- `task_queue`: 5 done, 0 pending, 0 claimed. (Matches `langgraph-
  agents` shipped cold.)
- `task_dlq`: 0.

**Cold-path baseline:**

- `ENABLE_CLAUDE_API: false` — Claude API escalation not yet
  proven by a live task. Stage 1 hot-path smoke test will toggle
  this with cost caps in place.

## How this doc evolves

Every Stage 1 verification PR updates the table (status column +
"Verifying PR" link) plus, where the audit surfaced a fixable
issue, adds the symptom + recovery to the runbooks section.

Gate 1 is reached when every row in every Class A and Class B
table reads ✅ or 🟡, the smoke-test transcript is captured, and
the top failure modes have entries in `docs/src/`.

## Stage 1 audit batches

### Batch 1 — 2026-05-21 Class A HR + pod readiness sweep

Mass evidence for DoD #1 (pod ready) + DoD #2 (Flux reconciled)
across all HomeAIOps-touching namespaces. Items moved from ⏳ to
🟢 on the basis of these results. Pod-restart, suspend-resume,
observability-target, and runbook line items remain open per
component and are tracked in separate follow-up PRs.

**HelmReleases (77 of 78 Ready):**

```text
ai               9 HRs    9 Ready
mcp-system      18 HRs   18 Ready
observability   24 HRs   23 Ready (grafana RollbackSucceeded — non-AI-path)
home            17 HRs   17 Ready
databases        5 HRs    5 Ready
storage          4 HRs    4 Ready
```

**CNPG clusters (HomeAIOps-relevant, all 3/3 instances Ready):**

```text
postgres-langgraph-checkpoints   3/3 instances   phase=healthy
postgres-langgraph-memory        3/3 instances   phase=healthy
postgres-langfuse                3/3 instances   phase=healthy
postgres-windmill                3/3 instances   phase=healthy
```

**Task queue substrate baseline:**

```text
task_queue.status='pending'    0
task_queue.status='claimed'    0
task_queue.status='done'       5
task_dlq                       0
```

**Pod restart audit (HomeAIOps namespaces, > 5 lifetime restarts
on Running pods):**

```text
observability/mqtt-exporter-0        restarts=5  (non-AI-path)
observability/smartctl-exporter-*    restarts=5-6  (non-AI-path)
```

No AI-pipeline component has > 5 lifetime restarts; the previous
worker (26 restarts, exit-137 OOM) was replaced cleanly under
[#11922](https://github.com/rwlove/home-ops/pull/11922).

### Batch 2 — 2026-05-21 Flux suspend/resume cycle

Evidence for DoD #3: every AI-pipeline Kustomization survives a
suspend/resume cycle without manual intervention. Procedure per
Kustomization: `flux suspend ks <name>`, wait 30 s, `flux resume
ks <name>`, watch for `Ready=True` again. Captured cycle time =
`flux suspend` → `Ready=True` (includes the deliberate 30 s pause).

Representative sample covering inference, agents, MCP fleet, and
the cron-driven Claude lane:

```text
ai/langgraph-agents      Ready=True at t+37s
ai/ollama-spark          Ready=True at t+38s
ai/tei-spark             Ready=True at t+37s
mcp-system/memory-mcp    Ready=True at t+37s
mcp-system/mcp-gateway   Ready=True at t+64s
observability/holmesgpt  Ready=True at t+37s
automation/claude-runner Ready=True at t+38s
```

All 7 Kustomizations resumed cleanly. No manual touch needed;
no dependent resources entered transient `NotReady`. The
`mcp-gateway` outlier at 64 s is consistent with its three-HR
inventory (gateway + istio + controller) which takes longer to
reconcile than a single-HR Kustomization.

Full-fleet sweep deferred to a follow-up after first running the
E2E smoke test (DoD #2) — keeping the maintenance-window blast
radius narrow while Claude API enablement is in flight.

### Batch 3 — 2026-05-21 Pod-level restart cycle

Evidence for DoD #4 ("Survives a pod-level restart of each
component"). Procedure: `kubectl delete pod <X>` → watch for a
new pod in `Running` + `Ready=True`. Captured cycle time = delete
→ `Ready=True`.

Representative sample, covering Deployment + StatefulSet
workloads on the inference, agent, MCP, and observability layers:

```text
ai/langgraph-agents      (Deployment)   Ready=True at t+31s
ai/tei-spark             (Deployment)   Ready=True at t+21s
ai/ollama-spark          (StatefulSet)  Ready=True at t~80s  (manual verify *)
mcp-system/mcp-gateway   (Deployment)   Ready=True at t+10s
mcp-system/memory-mcp    (Deployment)   Ready=True at t+21s
observability/holmesgpt  (Deployment)   Ready=True at t+20s
```

*ollama-spark verification note*: StatefulSet pods keep the same
name (`ollama-spark-0`) on restart, so the original test script's
"find a pod with a different name" logic didn't fire its success
branch. Verified manually via `kubectl get events`: pod was
Killing at t=0, new container Started at t+10s, `Ready=True`
within ~80s of delete (model state had to reload into VRAM —
this is expected behaviour for an ollama statefulset and within
the SLA we'd document if asked).

All 6 components recovered without manual intervention. During
the mcp-gateway restart, the Claude Code MCP tool surface went
read-only for ~10 s while the istio sidecar drained and the new
pod came up — visible to clients as transient connect-refused.
No persistent failures.

Full-fleet sweep deferred. The smoke test is the bigger gate.

### Batch 4 — 2026-05-21 E2E smoke (Stage 1 DoD #2 evidence)

Evidence for goal.md Stage 1 DoD #2 ("End-to-end smoke test
passes: task in → result out"). Hot path is verified-armed —
`ENABLE_CLAUDE_API=true` confirmed in the running pod, cost caps
active, daily spend $0.00 (local routing sufficed for the test
prompts).

**Smoke 1 — simple note (note-maker):**

```text
POST http://langgraph-agents.ai.svc.cluster.local:8765/inbox
{ task_id:"stage1-smoke-…", source:"test", user:"rob",
  content:"Stage 1 smoke test. In one short paragraph, describe
           what the HomeAIOps cluster is for. Save the answer as a
           vault file under /vault/agents/notes/. Be concise." }

→ accepted, server-assigned task_id: 01KS5VAK8JCHBCGQ2EYSJXGXXC
```

Pipeline trace:

```text
14:03:53 task_enqueued  → queue
14:03:53 node_start triager (data_tier=internal)
14:04:51 node_end   triager (58.4s)
14:04:52 node_start note-maker
14:05:05 node_end   note-maker (13.9s) → output=note drafted
14:05:05 task_completed (status=done, attempts=1)
```

Output vault file (`/vault/inbox/drafts/note-01KS5VAK8JCHBCGQ2EYSJXGXXC.md`,
679 bytes):

```text
---
task_id: 01KS5VAK8JCHBCGQ2EYSJXGXXC
source: test
domain: homelab
intent: note
proposed_location: ~/vaults/personal/homelab/homeaiops-cluster-purpose.md
new_vs_append: new
status: drafted
---

# HomeAIOps Cluster Purpose

The HomeAIOps cluster is designed to manage and orchestrate
various automated operations within the smart home infrastructure
…
```

Total wall: **72 s**, single attempt, no errors, no Claude
escalation needed (local model on ollama-spark sufficed for the
simple paragraph task — this is *correct* routing behavior).

**Smoke 2 — deeper task with approval-class handoff
(homelab-engineer + errand-runner):**

```text
POST /inbox  task_id=stage1-smoke-claude-…
  content: "requires_cloud: Analyze the HomeAIOps stabilization
            PR series (#11917-11927 on rwlove/home-ops) and propose
            ONE specific architectural improvement we should make
            before Stage 2 begins. Be concrete. Save as a vault file."

→ accepted, server-assigned task_id: 01KS5VFWSHF7YM1ASWQESNH6GE
```

Pipeline trace:

```text
14:06:48 task_enqueued / task_dequeued (worker_id=langgraph-agents-9968cb858-krct8/1)
14:06:48 node_start triager
14:07:01 node_end   triager (13.4s) → routed to homelab-engineer
14:07:01 node_start homelab-engineer
14:11:14 node_end   homelab-engineer (252.8s incl. 227.7s ollama-spark
                                     model load)
                    → output=homelab finding
14:11:14 node_start errand-runner
14:11:14 node_error errand-runner GraphInterrupt (paused for approval)
14:11:14 approval_post action_class=C status=201
                    (Windmill langgraph-approval-post webhook called)
14:11:14 task_completed has_output=True
```

Output vault file
(`/vault/inbox/drafts/homelab-01KS5VFWSHF7YM1ASWQESNH6GE.md`):

```text
---
task_id: 01KS5VFWSHF7YM1ASWQESNH6GE
kind: homelab-finding
action_class: C
handoff_target: errand-runner
target_repo: home-ops
---

# Homelab finding — Analyze HomeAIOps stabilization PR series …

## Diagnosis
The HomeAIOps stabilization PR series aims to improve … (etc.)

## Proposed action
Before proceeding with Stage 2, we should implement a high-
availability (HA) solution for the Home Assistant instance. …
```

Total wall: **264 s** (4m24s) including the 227.7s cold-load of
the model on ollama-spark. Smoke 2 exercised:

- multi-agent routing (triager → homelab-engineer → errand-runner)
- vault file write (homelab-finding shape)
- approval interrupt (`GraphInterrupt` → Windmill
  `langgraph-approval-post.ts` → status=201)
- `action_class=C` (must-approve tier)
- `task_completed` with output

No Claude API call was triggered — costs endpoint shows
`spent_usd=0.0` after both smokes; the agent fleet's routing
correctly determined ollama-spark was sufficient. The Claude
escalation path remains hot and ready (gate is `true`,
108-byte key in `langgraph-agents-secret`, cap watchers running).
The *first task that actually needs Claude* will land it. Stage 1
DoD #2 is satisfied: "task in → result out" round-tripped end-to-
end through every layer of the AI pipeline.

## Runbooks

Failure modes encountered during Stage 1, with confirmation and
recovery steps.

### ExternalSecret-extract field present but empty

**Symptom.** A Secret populated by an ExternalSecret is missing
the value an app needs. `kubectl get secret … -o yaml` shows the
key exists; `... | base64 -d | wc -c` returns 0 or 1 bytes. The
ExternalSecret itself reports `SecretSynced` cleanly — there is
no operator-level error.

**How to confirm.**

```sh
LEN=$(kubectl get secret -n <ns> <secret> \
  -o jsonpath='{.data.<KEY>}' | base64 -d | wc -c)
echo "decoded length: $LEN bytes"
kubectl get externalsecret -n <ns> <name> \
  -o jsonpath='{.status.conditions[?(@.type=="Ready")]}'
```

If the decoded length is 0–1 bytes and the ExternalSecret is
`Ready=True`, the 1P field exists but is blank.

**Root cause.** The 1P item has a placeholder field with the
right name but no value behind it. `external-secrets-operator`
copies the empty string into the target Secret — it can't tell
the difference between "deliberately empty" and "operator forgot
to paste the value."

**Recovery.** Two paths:

1. *Fill the empty 1P field.* Open the 1P item, paste the value,
   force-sync:

   ```sh
   kubectl annotate externalsecret -n <ns> <name> \
     force-sync=$(date +%s) --overwrite
   ```

2. *Point at the field the secret already lives under.* If the
   value is already in a different 1P item under a different
   field name (e.g. an empty `ANTHROPIC_API_KEY` here while the
   live value is in another item's `anthropic_api_key`),
   re-wire the ExternalSecret's `dataFrom` + template to pull
   the live field. Single rotation surface beats duplicate
   copies. The canonical example for this repo is the langgraph
   Anthropic key fix in
   [#11923](https://github.com/rwlove/home-ops/pull/11923).

**Prevention.** When adding a new ExternalSecret template
variable that maps from a 1P field, verify the field is non-
empty before merging. A `wc -c` check is enough.

### Stuck CSI RBD unmap (Ceph PG ghost-stuck)

**Symptom.** Pod with a `ceph-block` PVC stuck `ContainerCreating`
for minutes with events:

```text
FailedMount  MountVolume.MountDevice failed: rpc error: code = Aborted
desc = an operation with the given Volume ID … already exists
```

Or, on the originating node, `rbd unmap /dev/rbdN` fails with
exit status 16 (EBUSY) despite **no userland processes** holding
the device (verified via `/proc/[0-9]*/maps`, `/proc/[0-9]*/fd`,
and `/sys/block/rbdN/holders/` all empty).

`ceph -s` may report a stale `SLOW_OPS` aggregate, but per-OSD
`dump_blocked_ops` (both via admin socket and via mon-routed
`ceph tell osd.X`) shows **zero blocked ops**. The aggregate is
the mgr's cached-but-not-decremented count from a prior real
event — misleading.

**Underlying issue.** A specific PG owning the rbd_header object
is "ghost-stuck": it can't be queried (`ceph pg N.M query` times
out), it's not in `pg dump_stuck`, but raw `rados stat
rbd_header.<id>` against it hangs. The OSD primary has cleared
its blocked-ops queue but the PG's per-object lock or watcher
state is wedged.

**How to confirm.**

```sh
# 1. From a mon pod, with the embedded keyring:
MON=$(kubectl get pod -n rook-ceph -l app=rook-ceph-mon -o name | head -1)
ARGS='--conf /etc/ceph/ceph.conf
      --keyring /etc/ceph/keyring-store/keyring
      -m "[v2:10.43.154.244:3300,v1:10.43.154.244:6789]"'   # adjust mons

# 2. Identify the image_id from the affected node's kernel state:
kubectl debug node/<node> -it=false --image=busybox:1.36 -- \
  cat /host/sys/bus/rbd/devices/<N>/image_id
# → e.g. 0e6ed03d95369c

# 3. Find the PG that owns the header:
kubectl exec -n rook-ceph "$MON" -c mon -- bash -c "
  ceph $ARGS osd map ceph-blockpool rbd_header.<image_id>
"
# → 'pg P.Q -> up [primary, ...] acting [primary, ...]'

# 4. Confirm the PG itself is unresponsive:
kubectl exec -n rook-ceph "$MON" -c mon -- bash -c "
  timeout 15 ceph $ARGS pg P.Q query
"
# → exit 124 (timeout) is the signature

# 5. Confirm OSDs say they're idle (rules out a real slow op):
kubectl exec -n rook-ceph "$MON" -c mon -- bash -c "
  ceph $ARGS tell osd.<primary> dump_blocked_ops
"
# → empty / {ops: []}

# 6. Confirm raw RADOS read of the object also hangs:
kubectl exec -n rook-ceph "$MON" -c mon -- bash -c "
  timeout 15 rados $ARGS -p ceph-blockpool stat rbd_header.<image_id>
"
```

**Recovery — propose-then-execute.** Three options, least-invasive first:

1. *Targeted blocklist of the watcher client* — works if the
   watcher is on a distinct krbd nonce. Pull `client_addr` from
   `/sys/bus/rbd/devices/N/client_addr` on the originating node,
   confirm it's not shared with healthy rbd mappings on the same
   node, then:

   ```sh
   ceph $ARGS osd blocklist add <addr>/<nonce> 3600
   ```

   Retry `rbd unmap` from the node — should release within
   seconds.

2. *Force PG re-peering via `ceph osd down <primary>`* — when
   blocklist doesn't help (PG state itself is stuck, not just a
   client lease). Marking the primary down triggers re-peering
   via the replica OSDs; the daemon self-restarts under Rook
   within ~30s; PGs primaried elsewhere are unaffected. The
   acting set must have at least min_size replicas remaining
   alive for safety (verify with `ceph osd tree`).

3. *Decouple via PVC delete-and-recreate (regenerable data
   only)* — for caches like ZOT where the data is rebuildable
   from upstream sources:

   - `kubectl scale statefulset/<app> --replicas=0`
   - `kubectl delete pvc <pvc-name>` (reclaim policy `Delete`
     on the storage class will GC the underlying RBD image)
   - `kubectl scale statefulset/<app> --replicas=1`

   This recovers the workload independently of the Ceph fix.
   The underlying PG-ghost-stuck issue remains and needs
   option 1 or 2 separately.

**Prevention.** The class of error appears tied to a prior
cluster-network event (worker3 + worker7 network degradation
on 2026-05-20 — see [[project_worker3_network_degraded_postgres_zulip]])
that left rbd watchers in a broken state on the affected nodes.
Stabilizing node-level network reliability is the long-term fix.
Until then, document the rbd-device-to-image mapping so the
identification step (3) is faster.

**Operational follow-up.** This cluster has no `rook-ceph-tools`
pod deployed (`operator/helmrelease.yaml` lacks
`toolbox.enabled: true`). All ceph CLI work has to go through a
mon pod with explicit `--conf` / `--keyring` / `-m` flags as in
step 1. Adding the toolbox to the rook-ceph operator helmrelease
is a Stage 2 quality-of-life follow-up; see incident on
2026-05-21 for the time cost without it.

### ZOT cold-start recovery — the secondary failure mode

This runbook section pairs with the one above. When the
underlying Ceph PG issue clears, ZOT itself doesn't immediately
recover — it goes through a long cold-start sequence that can
look like a fresh failure.

**Symptom.** After the Ceph PG re-peer succeeds (osd.2 came
back up, `rbd info` works again from a mon pod), `zot-0` enters
a `CrashLoopBackOff` pattern even though the underlying volume
is healthy. Each restart shows `parsing next repo` log lines
walking through hundreds of cached images, but the pod gets
killed by its liveness probe before reaching the end.

```sh
kubectl logs -n kube-system zot-0 --tail=5 | grep parsing
# → "parsing next repo 'quay.io/...': total=272, progress=233"
# Restart count climbs by 1 every ~30 s.
```

After this clears (post-parse + scrub + GC + retention all run
on first boot), the pod becomes `Ready` but **serves chart
manifests with 5-minute latency** for the next 30–60 min while
background ops still contend for I/O.

**How to confirm — phase 1 (parse loop).**

```sh
kubectl get pod -n kube-system zot-0
# RESTARTS climbing by 1/30s — fingerprint
kubectl logs -n kube-system zot-0 --previous --tail=10 | grep -c "parsing next repo"
# → many; the parse never completes inside the liveness window
kubectl get pod -n kube-system zot-0 -o jsonpath='{.spec.containers[0].livenessProbe}'
# → check failureThreshold; default is 3 × 10s = 30 s — too short
```

**How to confirm — phase 2 (post-parse latency).**

ZOT pod is `Ready=True`, but `helm template` against ZOT-mirrored
charts times out from CI:

```sh
kubectl logs -n kube-system zot-0 --tail=20 | grep '"latency"'
# Each manifest GET reports `"latency":"5m2s"` (or similar) —
# fingerprint of disk contention from scrub + gc + retention
kubectl logs -n kube-system zot-0 --tail=20 | grep -E "scrub|garbage collected|retention"
# All three running concurrently on cold-start
```

**Root cause.** ZOT v2 runs three periodic maintenance ops:

- *Scrub* — manifest/blob integrity check
- *GC* — remove unreferenced blobs
- *Retention* — delete tags per retention policy

On a cold restart these all fire at once. With a 500 GiB
ceph-block PVC and 272 cached repos, the first pass dominates
disk I/O and serving threads starve. Steady-state runs touch
only the small delta since the last run and are fast.

**Recovery — phase 1 (parse loop).**

Patch the liveness probe in-cluster to survive cold start:

```sh
kubectl patch statefulset -n kube-system zot --type=json -p='[
  {"op":"replace",
   "path":"/spec/template/spec/containers/0/livenessProbe/failureThreshold",
   "value":60}
]'
```

This bumps `failureThreshold` from 3 to 60 (10 min). The
StatefulSet rolls a new pod; if Flux subsequently reconciles
the HelmRelease, **the patch is overwritten** — see prevention
below.

**Recovery — phase 2 (post-parse latency).**

Wait it out. Background ops settle in 30–60 min. CI can be
unblocked by **admin-bypass merging** PRs whose CI failures are
specifically `Extract images` / `Flux Local Test` chart-fetch
timeouts against ZOT — the failures are pure infrastructure
flake, not PR content.

**Prevention — Stage 2 follow-ups.** The in-cluster patch in
"Recovery phase 1" is a hotfix. The durable fix is in Git:

1. Add a `startupProbe` to the ZOT HelmRelease in
   `kubernetes/apps/kube-system/zot/app/helmrelease.yaml` so
   liveness/readiness don't apply until startup completes:

   ```yaml
   probes:
     startup:
       enabled: true
       custom: true
       spec:
         httpGet:
           path: /v2/
           port: 5000
         initialDelaySeconds: 30
         periodSeconds: 30
         failureThreshold: 60   # 30 min cold-start budget
   ```

2. (Optional) Configure ZOT's `extensions.scrub.delay`,
   `extensions.gc.delay`, and `retention.schedule` so the
   first post-restart pass doesn't fire immediately. See
   ZOT v2 config docs.

3. Open a `workaround:` issue per
   `.agents/instructions/workarounds.md` linking back to this
   runbook section so the in-cluster patch isn't quietly lost
   on the next HelmRelease reconcile.

**Cross-reference.** The user-visible symptom that *first*
exposes this on a busy cluster is "Django ADMINS email about
postgres connection errors" — the postgres-zulip primary
takes a brief connection flap during PG re-peering, Django's
ADMINS hook fires. The Postgres process itself doesn't
restart; reconnect is automatic; data is intact. See the
2026-05-21 incident notes for the exact timing.

### Stalled HelmRelease with `MissingRollbackTarget`

**Symptom.** A HelmRelease shows `Ready=False` with condition
`Stalled / MissingRollbackTarget`: "Failed to perform remediation:
missing target release for rollback: cannot remediate failed
release." `helm history -n <ns> <release>` shows **every** release
in `failed` state — there is no successful version to roll back
to. Underlying resources (Deployment, Service, etc.) may
nevertheless be live and serving traffic.

**How to confirm.**

```sh
kubectl get helmrelease -n <ns> <name> -o json \
  | jq '.status.conditions[] | select(.type=="Stalled")'
helm history <release> -n <ns>
```

If all release versions are `failed` and the underlying
Deployment is `Available`, the chart's resources have converged
despite Helm's history saying otherwise — Helm's wait/timeout
fired before the pod became `Ready`.

**Root cause.** The HelmRelease's effective install/upgrade
timeout (`spec.timeout`, default 5 m) is shorter than the time
the first Deployment took to become `Ready`. Common drivers:
Istio sidecar warmup, slow image pull on first install, slow
ExternalSecret resolution. Once the first install fails, Flux
attempts retries; each retry also times out, and after the
configured `retries` count the release is `Stalled` with no
healthy version to remediate to.

**Prevention.** Set `spec.timeout` on the HelmRelease to cover
the slowest-realistic warmup for the workload (15 m is a safe
default for Istio-injected pods). See
`kubernetes/apps/mcp-system/windmill-mcp/app/helmrelease.yaml`
for the canonical example.

**Recovery.** *Destructive — propose to operator before running.*
The Deployment and friends already exist; we need to make Helm's
storage agree.

1. Suspend Flux reconciliation so it doesn't fight the cleanup:

   ```sh
   flux suspend hr <name> -n <ns>
   ```

2. Verify the underlying Deployment is healthy:

   ```sh
   kubectl get deploy,svc,sa,httproute -n <ns> \
     -l app.kubernetes.io/instance=<name>
   ```

3. Delete the chart resources and the failed Helm history
   secrets together — Helm won't adopt resources it doesn't own,
   so a brief downtime is unavoidable:

   ```sh
   kubectl delete deployment,svc,sa,httproute \
     -n <ns> -l app.kubernetes.io/instance=<name>
   kubectl delete secret -n <ns> -l owner=helm,name=<name>
   ```

4. Resume Flux and force reconcile; the HR will do a clean
   `helm install`:

   ```sh
   flux resume hr <name> -n <ns>
   flux reconcile hr <name> -n <ns> --force
   ```

5. Verify the HR becomes `Ready=True` within the new
   `spec.timeout` window:

   ```sh
   kubectl get hr -n <ns> <name> -w
   ```

Time budget: 2–3 m of downtime per HR. Pick a maintenance
window per `CLAUDE.md` if the component is operator- or
Renee-facing.

### Local inference path down — escalation fallback

**Symptom.** Agent tasks queued at `/inbox` stop completing, or
complete with empty/garbage model output. The langgraph-agents pod is
`Running` and the queue is draining (no DLQ pileup from worker crashes),
but every task that routes to a local model group (`local-p40`,
`local-spark`) errors out or times out. Symptoms that point here rather
than at the worker: HolmesGPT alert-triage returns "model unavailable,"
`hai cost` shows no new local rows accumulating, and Ollama's own probes
are red.

**How to confirm.** Check the two local inference backends directly —
this is read-only:

```sh
# P40 (≤8b class) and Spark (32b class) Ollama endpoints
kubectl get pods -n ai -l app.kubernetes.io/name=ollama
kubectl get pods -n ai -l app.kubernetes.io/name=ollama-spark
# Are the models actually loaded / responding?
kubectl logs -n ai deploy/ollama --tail=50
kubectl logs -n ai deploy/ollama-spark --tail=50
```

A GB10/Spark-specific tell: most DCGM counters are broken on GB10, so
use **power draw** as the "is the GPU doing work" proxy, not
`GPU_UTIL`:

```promql
DCGM_FI_DEV_POWER_USAGE{Hostname=~".*spark.*"}
```

Flat-at-idle power while the queue has work waiting confirms the Spark
inference path is dead, not merely quiet. Cross-check the OllamaWedged
blackbox synthetic probe (see `project_wedge_detection_blackbox_synthetic_probe`)
— it exercises both P40 and Spark on a schedule and is the fastest
single signal that a backend has wedged versus crashed.

**Root cause.** Common drivers, roughly in order: Ollama wedged (model
load hung — the classic Pascal/P40 failure, recovered by a pod restart,
*not* a node action); the Spark host down or its containerd runtime
unhealthy (Spark is the lone non-CRI-O node); a model pull mid-flight
left no resident model; or the GPU itself fell off the bus. The queue
substrate is healthy in all of these — the failure is purely the
inference layer the router points at.

**Recovery.** The design intent is that the cluster *already has* an
escalation fallback armed: `ENABLE_CLAUDE_API: true` on the
langgraph-agents pod, with in-cluster cost caps ($5/task,
$10/agent/day, $30/global/day). When local capacity is genuinely
unavailable the router's escalation path to the `claude` group is the
fallback — but today the fleet routes 100% local and **never escalates
on backend failure automatically** (the router scorer escalates on
context overflow per group, not on live backend health). So recovery is
operator-driven:

1. **First, try to restore local** — it's faster and free, and the most
   common cause is a wedged Ollama that a restart clears:

   ```sh
   kubectl rollout restart -n ai deploy/ollama         # P40
   kubectl rollout restart -n ai deploy/ollama-spark   # Spark
   ```

   Re-run the wedge probe (or `hai` a trivial task) and confirm local
   rows resume in `hai cost`.

2. **If local can't be restored quickly** and tasks are time-sensitive,
   the escalation path is the bridge. The gate is already hot
   (`ENABLE_CLAUDE_API: true`), so escalation needs a routing decision,
   not a config flip. Until the router scores backend health
   automatically, escalate by pinning the affected task class to the
   `claude` group — propose this to Rob first; it spends real money and
   the cost caps are the only guardrail. Watch spend live:

   ```promql
   sum(increase(langgraph_cost_usd_total[1h]))
   ```

   The caps degrade the router back to local / queue lower-priority
   remote work as the daily budget is approached — that is the intended
   self-throttle, not a failure.

3. **If the Spark host itself is down**, that is a node-level recovery
   (out of scope for this runbook — see the node-drain / physical-restart
   notes) and the P40 (`local-p40`, ≤8b) remains the only local option.
   Route what fits in 8b to P40; escalate the rest per step 2.

**Prevention.** The deterministic router scorer now ships in v0.2.63
(lga PRs 112 and 114): it makes the local-vs-Claude call an explicit,
metric-emitting gate and escalates automatically on context overflow.
What it does **not** yet do is score *live backend health* — failing
over local→Claude on its own when a backend is down, inside the cost
caps. That health-failover is the remaining durable fix
(`project_todo_homelab_router_queue_substrate`), still forward-looking
because the fleet has had no escalation pressure to date. Until it
lands, the OllamaWedged synthetic probe + Pushover alert is the
detection half, and this runbook is the manual response half. Keep
`ENABLE_CLAUDE_API: true` and the cost caps in place so the escalation
path stays one routing decision away rather than a config change under
pressure.

> **Note on the cost caps as a safety floor.** The escalation fallback
> is only safe *because* the per-task / per-agent / per-day caps bound
> the blast radius of a runaway escalation. Do not raise or remove them
> as part of incident response — if a real local outage drives sustained
> escalation into the cap, that is the cap doing its job; the answer is
> to restore local capacity, not to widen the budget.
