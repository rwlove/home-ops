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
| AlertManager firing alert → HolmesGPT | A | ⏳ | — |
| Cron schedules (Windmill + k8s CronJob) | A | ⏳ | — |
| Operator tap on ntfy | A | ⏳ | — |

### Bridges (Windmill TS workflows)

| Workflow | Class | Status | Verifying PR |
|---|---|---|---|
| `langgraph-inbox.ts` | A | ⏳ | — |
| `zulip-triager-webhook.ts` | A | ⏳ | — |
| `alertmanager-holmesgpt-notify.ts` | A | ⏳ | — |
| `langgraph-daily-digest.ts` | A | ⏳ | — |
| `langgraph-cost-cap-watcher.ts` | A | ⏳ | — |
| `langgraph-awaiting-user-sweep.ts` | A | ⏳ | — |
| `langgraph-dlq-watcher.ts` | A | ⏳ | — |
| `langgraph-approval-post.ts` | A | ⏳ | — |
| `langgraph-approval-receive.ts` | A | ⏳ | — |
| `paperless-rag-ingest.ts` | A | ⏳ | — |
| `paperless-rag-tombstone.ts` | A | ⏳ | — |
| `workaround-watcher.ts` | A | ⏳ | — |

### Agents

| Agent | Class | Status | Verifying PR |
|---|---|---|---|
| HolmesGPT | A | 🟢 batch-audit pass (HR Ready, pod Running 4h+) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| langgraph-agents (substrate) | B | 🟢 substrate batch-audit pass (HR Ready, pod 0.2.33 Running 3h+, queue `task_queue` 5 done / 0 pending / DLQ 0) — Claude API gate still cold, see [#11923](https://github.com/rwlove/home-ops/pull/11923) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| supervisor (langgraph specialist) | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| researcher | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| coder | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| reviewer | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| triager | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| reporter | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| note-maker | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| homelab-engineer | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| smart-home-engineer | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| ml-tuner | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| errand-runner (local-only pin) | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| property-coordinator | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| health-tracker (local-only pin) | B | 🟡 cold via substrate — exercise pending E2E smoke | — |
| claude-runner pr-triage | A | 🟢 batch-audit pass (CronJob 13:00 UTC daily, last successful Completed pod visible) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| claude-runner cost-cap-commentary | A | 🟢 batch-audit pass (CronJob 22:00 UTC daily) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| doc-writer / Scribner | C | 🟥 n/a | — |

### Inference backends

| Backend | Class | Status | Verifying PR |
|---|---|---|---|
| ollama (P40) | A | 🟢 batch-audit pass (HR `Ready=True / UpgradeSucceeded`, pod 2d17h uptime, 0 restarts) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| ollama-spark (GB10) | A | 🟢 batch-audit pass (HR `Ready=True`, pod 41h uptime, 0 restarts) | [#11924](https://github.com/rwlove/home-ops/pull/11924) |
| Claude API (via langgraph) | B | 🚧 cold-gate (`ENABLE_CLAUDE_API: false`); secret-wiring single-source in [#11923](https://github.com/rwlove/home-ops/pull/11923) | [#11923](https://github.com/rwlove/home-ops/pull/11923) |
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
| Ntfy push (tap-to-approve) | A | 🚧 `home/ntfy` HR Ready — push exercise pending E2E smoke | — |
| Langfuse traces (OTLP sink) | A | 🚧 sink ready (Langfuse stack Ready post-#11922); first OTLP frame pending langgraph hot-path | — |

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
- khoj + khoj-oauth2-proxy
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
