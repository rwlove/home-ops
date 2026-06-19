# Routing Policy — Local vs Claude Escalation

## What this is

`kubernetes/apps/ai/langgraph-agents/routing-policy.yaml` is the
version-controlled source of truth for how HomeAIOps decides whether
a task runs on a local Ollama model or escalates to the Anthropic
Claude API.

The policy is an explicit ruleset — no learned router yet. First-match
wins. Changing the file changes behavior (once the Stage 3 execution
work wires the ConfigMap into the runtime; see **Status** below).

This is a Stage 3 artifact. Per `goal.md`:

> "Version-controlled file in the repo that decides local vs. escalate
> based on explicit rules (task type, model capability, context size,
> sensitivity). No learned router yet — explicit rules first. Changing
> the file changes behavior."

## Status

**Draft / not yet wired (as of initial commit).**

The ConfigMap is committed to the repo and will be reconciled by Flux
into the `ai` namespace, but the langgraph-agents runtime does not yet
read it. Routing decisions are still made by the hardcoded `AGENT_GROUP`
dict and `llm()` factory in `src/agents/llm.py`. Wiring the
ConfigMap into the runtime is Stage 3 execution work, gated on Gate 2
(dogfooding sign-off).

Until wiring lands, this file is the declarative statement of *intent* —
the target routing behavior that code will be written to enforce.

## Where it lives

```text
kubernetes/apps/ai/langgraph-agents/
├── routing-policy.yaml        ← ConfigMap (this policy)
├── ks.yaml
└── app/
    ├── helmrelease.yaml
    └── kustomization.yaml
```

The ConfigMap is at the `langgraph-agents/` level rather than `app/`
because it is not yet part of the Flux-reconciled `app` path
(`spec.path: ./kubernetes/apps/ai/langgraph-agents/app`). Once the
runtime wiring is ready, it will move into `app/` and be added to
`app/kustomization.yaml`.

## How to change the policy

1. Edit `kubernetes/apps/ai/langgraph-agents/routing-policy.yaml`.
   The YAML inside the ConfigMap's `data.routing-policy.yaml` key is
   the ruleset.
2. Open a PR. CI lints the file; a reviewer checks that no rule
   accidentally routes restricted-tier data to Claude.
3. Merge. Flux reconciles the ConfigMap into the `ai` namespace.
4. The langgraph-agents pod does **not** need to restart — the runtime
   (once wired) will hot-reload the policy from the mounted ConfigMap.

No code change is required to add, remove, or reorder rules once the
runtime wiring lands.

## Rule evaluation

Rules are evaluated in document order. The first rule whose `match`
block matches all specified keys wins. If no rule matches, the `default`
route applies (currently `local-spark`, i.e. qwen3-next:80b-a3b-instruct-q4_K_M on Spark).

**Match keys** (all optional; combined with AND):

| Key | Type | Example |
|---|---|---|
| `task_class` | string | `summarization`, `architecture`, `code-edit` |
| `agent_id` | string | `health-tracker`, `coder`, `triager` |
| `data_tier` | string | `public`, `internal`, `restricted` |
| `context_tokens` | object | `{gt: 50000}` |
| `escalate_flag` | bool | `true` |
| `spark_healthy` | bool | `false` (runtime-resolved) |
| `p40_healthy` | bool | `false` (runtime-resolved) |

**Route values:**

| Value | Hardware | Model |
|---|---|---|
| `local-p40` | P40 (Pascal, 24 GB) | `qwen2.5:7b` |
| `local-spark` | DGX Spark (GB10) | `qwen3-next:80b-a3b-instruct-q4_K_M` |
| `local-spark-coder` | DGX Spark (GB10) | `qwen2.5-coder:32b` |
| `claude` | Anthropic API | `claude-sonnet-4-6` (default) |
| `local-only` | whichever path is healthy | per rule; raises if both down |

## Current rules — rationale

### Hard pins (never escalate)

- **`health-tracker` → `local-only`**: Health data never leaves the
  cluster. Hard constraint in `IDENTITY.md`; cannot be overridden by
  any other rule.
- **`data_tier: restricted` → `local-only`**: Restricted-tier tasks
  are already blocked at runtime by `redaction.py`, but the policy
  makes the intent explicit and greppable.

### Explicit escalation

When a node calls `llm(agent_id, escalate=True)` and the data tier
permits remote emission, the policy routes to `claude-sonnet-4-6`.
Cost caps in `settings.py` still apply regardless.

### Task class routing

Local-first classes (no context size or quality argument for Claude):

| Task class | Route | Reason |
|---|---|---|
| `summarization` | `local-spark` | 32b handles well; no escalation needed |
| `classification` | `local-p40` | Binary/short-label; 7b sufficient |
| `log-triage` | `local-spark` | Error-pattern extraction; 32b |
| `doc-drift` | `local-p40` | Structural conformance; 7b |
| `note-taking` | `local-p40` | Simple drafting; 7b |
| `alert-triage` | `local-spark` | Needs reasoning; 32b |
| `code-edit` | `local-spark-coder` | Dedicated coder model |
| `code-review` | `local-spark-coder` | Dedicated coder model |
| `image-generation` | `local-spark` | ComfyUI prompt composition |

Claude-first classes (complexity or novelty exceeds reliable local
capability):

| Task class | Route | Reason |
|---|---|---|
| `architecture` | `claude` | Novel decisions; local models insufficient |
| `research` | `claude` | Open-ended; benefits from broad knowledge + tool use |

### Context size threshold

Requests exceeding 50,000 tokens escalate to Claude (`claude-sonnet-4-6`).
The 50k threshold reflects qwen3-next:80b-a3b-instruct-q4_K_M's reliable quality window —
the model's nominal context is 128k, but quality degrades significantly
above ~64k. 50k is a conservative gate that avoids truncation or quality
collapse on borderline cases.

Adjust the threshold in the policy YAML; no code change needed.

### Degraded mode

When both Ollama endpoints (Spark and P40) are unhealthy, `public` and
`internal` tier tasks escalate to Claude. `restricted` tier tasks fail
with an error rather than escalate. This mirrors the
`degraded_mode_escalation_enabled` env-var behavior in `llm.py` and
makes it policy-explicit.

### Agent-specific overrides

Every agent in `AGENT_GROUP` (in `llm.py`) has a matching rule that
pins it to the same group. These rules make the agent→model assignment
greppable from the policy file without needing to read Python source.

P40 (`qwen2.5:7b`) agents: `triager`, `note-maker`, `errand-runner`,
`property-coordinator`, `doc-writer`, `health-tracker`.

Spark general (`qwen3-next:80b-a3b-instruct-q4_K_M`) agents: `historian`, `researcher`,
`supervisor`, `reporter`, `homelab-engineer`, `network-operator`,
`storage-operator`, `smart-home-operator`, `ml-operator`,
`observability-operator`, `security`, `auditor`, `artist`.

Spark coder (`qwen2.5-coder:32b`) agents: `coder`, `reviewer`.

## Cost / spend guardrails

The policy file declares the guardrail thresholds for documentation and
future code use. Current values (match `helmrelease.yaml` env vars):

| Guardrail | Value |
|---|---|
| Per-task cap | $5.00 USD |
| Per-agent daily cap | $10.00 USD |
| Global daily cap | $30.00 USD |
| Escalation rate warn | 20% of tasks |
| Escalation spend warn | $10.00 USD/day |

**These are not yet enforced by the policy file itself.** Runtime
enforcement lives in `settings.py` + `observability.py` in langgraph-agents.
The policy fields are future wiring targets.

## Verifying routing decisions

Once the runtime wiring lands, every routing decision will be logged with
the matched rule and reason. Until then, verify by reading the current
hardcoded `AGENT_GROUP` dict in
`langgraph-agents/src/agents/llm.py`.

### Check current effective routing (pre-wiring)

```sh
# What group is each agent assigned to today?
kubectl exec -n ai deploy/langgraph-agents -- \
  python3 -c "from agents.llm import AGENT_GROUP; import json; print(json.dumps(AGENT_GROUP, indent=2))"
```

### Check escalation spend

```sh
# Total Claude spend today (from Prometheus)
kubectl exec -n ai deploy/langgraph-agents -- \
  python3 -c "from agents.observability import global_claude_spend_usd; print(global_claude_spend_usd())"

# Or query Prometheus directly:
# langgraph_cost_usd_total{group="claude"}
```

### Check that a restricted-tier task does not escalate

```sh
# Trigger a test task with data_tier=restricted and escalate=True.
# Expected result: RestrictedTierEmissionBlocked exception in logs;
#   no ANTHROPIC_API_KEY call made; spend counter unchanged.
kubectl logs -n ai deploy/langgraph-agents --since=2m | grep -i "restricted\|emission_blocked"
```

## Runbook — "local infra is down, fall back to Claude Code directly"

When both Ollama endpoints are down AND the task cannot wait for
degraded-mode escalation through the pipeline (e.g., the queue worker
itself is unhealthy):

1. Confirm both Ollama endpoints are unhealthy:

   ```sh
   curl -s http://ollama.ai.svc.cluster.local:11434/api/tags | jq '.models | length'
   curl -s http://ollama-spark.ai.svc.cluster.local:11434/api/tags | jq '.models | length'
   # If either curl times out or returns 0 models, that path is down.
   ```

2. Work directly in Claude Code against the repo. The routing policy
   does not gate Claude Code sessions — it gates the in-cluster pipeline.

3. File the outage as a P0 if Spark is down (primary inference for most
   agents), P1 if only P40 is down (affects triager / errand-runner /
   note-maker class agents).

4. When local inference recovers, tasks parked with
   `status=awaiting_ollama_recovery` in `task_queue` will be retried
   automatically by the queue poller.

5. Check the guardrail spend after recovery — if degraded-mode escalation
   ran for an extended period, the global daily spend counter may be
   elevated. The Grafana `aihomeops-state` dashboard shows per-day Claude
   spend and escalation rate.

## See also

- `docs/src/ai_architecture.md` — component map showing where the router
  sits in the pipeline
- `docs/src/homeaiops_dod.md` — Stage 3 definition of done
- `kubernetes/apps/ai/langgraph-agents/app/helmrelease.yaml` — current
  cost cap env vars (until wired from this ConfigMap)
- `langgraph-agents/src/agents/llm.py` — current routing implementation
- `langgraph-agents/src/agents/redaction.py` — restricted-tier emission gate
- `goal.md` — Stage 3 routing, escalation, and provenance requirements
