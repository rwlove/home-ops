# kagent

[kagent](https://kagent.dev) (CNCF sandbox) — a Kubernetes-native agent runtime.
Agents, models, and tools are CRDs; agents run on local models and are consumable
by Claude Code over MCP. This app is the **controller install**; the agent
definitions themselves live elsewhere (see *Agent CRDs* below).

Part of the two-tier agent architecture (Claude cockpit + kagent local operators).
Full design, transition sequence, and kill-criteria:
`~/.claude-personal/plans/wild-tumbling-goblet.md`.

## What this deploys

| File | Purpose |
|---|---|
| `app/ocirepository-crds.yaml` + `helmrelease-crds.yaml` | `kagent-crds` chart (CRDs) — installed first |
| `app/ocirepository.yaml` + `helmrelease.yaml` | `kagent` controller chart (dependsOn crds) |
| `app/values.yaml` | local-model config + trial guardrails (fed via `configMapGenerator`) |

Chart version **0.10.1** (pinned; `v1.0.0-alpha*` exists but we track the stable line).
Renovate will bump the `OCIRepository` tags.

## Trial posture (deliberate)

- **Controller-only.** Every built-in agent (`k8s-agent`, `observability-agent`,
  `promql-agent`, the `cilium-*` agents, …) is `enabled: false`. Enable one at a
  time, mapped to an operator persona, once its RBAC + model path are validated.
- **Local inference only.** `providers.default: ollama` → `ollama.ai.svc.cluster.local:11434`
  (`qwen2.5:7b`). No cloud provider keys; the native Ollama provider is keyless
  (avoids [kagent#57](https://github.com/kagent-dev/kagent/issues/57)).
- **Contained to `ai`.** `rbac.namespaces: [ai]` → the controller watches only `ai`
  (namespaced Role/RoleBinding, not a cluster-wide ClusterRole). Widen deliberately.
- **No custom CiliumNetworkPolicy** — the `ai` namespace baseline already allows the
  controller's egress (apiserver, DNS, intra-namespace). Agents that later reach
  external hosts/MCP will need their own `cnp-allow.yaml`.
- **Bundled dev Postgres** on `ceph-block` (regenerable). Not for production.

## Verify before merge

Values were verified against `helm show values ...@0.10.1`, but confirm the render:

```sh
flux build ks kagent --path ./kubernetes/apps/ai/kagent/app   # or: kustomize build
helm template kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent --version 0.10.1 \
  -f kubernetes/apps/ai/kagent/app/values.yaml | less
```

Ship suspended → manual-verify the controller comes up and the generated
`ModelConfig` reaches ollama → then let it reconcile (mirrors the claude-runner
trial discipline).

## Agent CRDs (not here)

Agent personas carry Internal/Restricted content, and this repo is **public**, so
`Agent` CRDs live in a **separate private Flux source**, not in home-ops. Personas
hold *method, not private facts* (fetch specifics at runtime via tools). The
canonical persona source is `~/.claude-personal/agents/*.md`.

## Production follow-ups

- Point `database.postgres.url` at a CNPG cluster with **pgvector**
  (`vectorEnabled: true`) to enable long-term agent memory; retire the bundled DB.
- `ui.enabled: true` + an HTTPRoute behind **Authelia** for the kagent web UI.
- `otel.tracing.enabled: true` → the cluster OTLP/Tempo endpoint.
- Expose the controller's agents over MCP behind the lovenet gateway for Claude
  Code delegation.
- **grafana-mcp** (enabled Phase 3a) pulls `docker.io/mcp/grafana:latest` — the parent
  chart exposes no image override and pins `:latest` (hence the allowlist entry). It also
  duplicates `mcp-system/grafana-mcp`. Follow-up: pin it, or point the observability agent
  at the existing gateway grafana-mcp instead of bundling a second one.

## Revert

Drop `./kagent/ks.yaml` from `kubernetes/apps/ai/kustomization.yaml` and delete this
directory → Flux prunes the controller, CRDs, and the (regenerable) bundled PVC.
