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
| HolmesGPT | A | 🟢 batch-audit pass (HR Ready, pod Running 4h+) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| langgraph-agents (substrate) | B | 🟢 substrate batch-audit pass (HR Ready, pod 0.2.33 Running 3h+, queue `task_queue` 5 done / 0 pending / DLQ 0) — Claude API gate still cold, see [#11923](https://github.com/rwlove/home-ops/pull/11923) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
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
| claude-runner pr-triage | A | 🟢 batch-audit pass (CronJob 13:00 UTC daily, last successful Completed pod visible) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| claude-runner cost-cap-commentary | A | 🟢 batch-audit pass (CronJob 22:00 UTC daily) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| doc-writer / Scribner | C | 🟥 n/a | — |

### Inference backends

| Backend | Class | Status | Verifying PR |
|---|---|---|---|
| ollama (P40) | A | 🟢 batch-audit pass (HR `Ready=True / UpgradeSucceeded`, pod 2d17h uptime, 0 restarts) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| ollama-spark (GB10) | A | 🟢 batch-audit pass (HR `Ready=True`, pod 41h uptime, 0 restarts) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| Claude API (via langgraph) | B | 🚧 cold-gate (`ENABLE_CLAUDE_API: false`); secret-wiring single-source in [#11923](https://github.com/rwlove/home-ops/pull/11923) | [#11923](https://github.com/rwlove/home-ops/pull/11923) |
| Claude Code (via claude-runner) | A | 🟢 batch-audit pass (CronJob-driven, `claude-runner-secret` has live 108-byte key) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| tei-spark (reranker) | A | 🟢 batch-audit pass (HR `Ready=True`, pod 4h32m uptime, 0 restarts) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |

### Tool surfaces

| Tool | Class | Status | Verifying PR |
|---|---|---|---|
| MCP Gateway (`mcp-gateway` + `-istio` + `-controller`) | A | 🟢 batch-audit pass (3 HRs Ready, all 3 pods Running 2d+) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| `arr-mcp` | A | 🟢 batch-audit pass (Running 11d) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| `chrome-mcp` | A | 🟢 batch-audit pass (Running 2d2h) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| `cilium-mcp` | A | 🟢 batch-audit pass (Running 17h) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| `github-mcp` | A | 🟢 batch-audit pass (Running 6d) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| `grafana-mcp` | A | 🟢 batch-audit pass (Running 7d6h) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| `ha-mcp` | A | 🟢 batch-audit pass (Running 6d20h) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| `immich-mcp` | A | 🟢 batch-audit pass (Running 25h) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| `kubectl-mcp` | A | 🟢 batch-audit pass (Running 17d) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| `memory-mcp` | A | 🟢 batch-audit pass (Running 27h; schema-init job Completed) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| `netbox-mcp` | A | 🟢 batch-audit pass (Running 41h) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| `omada-mcp` | A | 🟢 batch-audit pass (Running 17d) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| `paperless-mcp` | A | 🟢 batch-audit pass (Running 24h) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| `prometheus-mcp` | A | 🟢 batch-audit pass (Running 16d) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| `searxng-mcp` | A | 🟢 batch-audit pass (Running 17d) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| `time-mcp` | A | 🟢 batch-audit pass (Running 41h) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| `windmill-mcp` | A | ✅ verified (HR recovered via preventive timeout bump alone — destructive procedure not needed) | [#11919](https://github.com/rwlove/home-ops/pull/11919) |
| Qdrant | A | 🟢 batch-audit pass (HR Ready in databases ns) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| Postgres CNPG `langgraph-checkpoints` (task_queue + task_dlq + checkpoints) | A | 🟢 batch-audit pass (3/3 instances, phase=healthy) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| Postgres CNPG `langgraph-memory` (pgvector KG) | A | 🟢 batch-audit pass (3/3 instances, phase=healthy) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| Postgres CNPG `langfuse` | A | 🟢 batch-audit pass (3/3 instances, phase=healthy) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |

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
| `langfuse-web` | A | 🟢 batch-audit pass (Running 25h, HR `langfuse@1.5.31` Ready) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| `langfuse-worker` | A | 🟢 batch-audit pass (post-[#11922](https://github.com/rwlove/home-ops/pull/11922), pod recreated with 1Gi mem limit) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| `langfuse-clickhouse` (3-shard) | A | 🟢 batch-audit pass (3 shards Running 25h, 12Gi mem limit each) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| `langfuse-redis` | A | 🟢 batch-audit pass (Running 19h) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| `langfuse-s3` | A | 🟢 batch-audit pass (Running 25h) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |
| `langfuse-zookeeper` (3-node) | A | 🟢 batch-audit pass (post-[#11922](https://github.com/rwlove/home-ops/pull/11922), all 3 pods rolled to 1Gi mem limit; usage 115-203 MiB) | [#11925](https://github.com/rwlove/home-ops/pull/11925) |

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

## Runbooks

Failure modes encountered during Stage 1, with confirmation and
recovery steps.

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
