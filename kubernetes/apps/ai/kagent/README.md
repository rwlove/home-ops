# kagent

[kagent](https://kagent.dev) (CNCF sandbox) — a Kubernetes-native agent runtime.
Agents, models, and tools are CRDs; agents run on **local models** and are
consumable by Claude Code over MCP. This app deploys **both** the controller
**and** the in-repo agent fleet.

Part of the two-tier agent architecture (Claude cockpit + kagent local operators).
Full design, transition sequence, and kill-criteria:
`~/.claude-personal/plans/wild-tumbling-goblet.md`. Tool federation through the
Kuadrant mcp-gateway: `docs/src/kagent_mcp_gateway_migration.md`.

## What this deploys

| File | Purpose |
|---|---|
| `app/ocirepository-crds.yaml` + `helmrelease-crds.yaml` | `kagent-crds` chart (CRDs) — installed first |
| `app/ocirepository.yaml` + `helmrelease.yaml` | `kagent` controller chart (dependsOn crds) |
| `app/values.yaml` | local-model config + guardrails (fed via `configMapGenerator`) |
| `app/agent-*.yaml` (17) | the operator + concierge `Agent` CRDs |
| `app/agenttemplate-*.yaml` (4) | shared `AgentTemplate`s for the operator personas |
| `app/modelconfig-litellm.yaml` / `modelconfig-vllm-spark.yaml` | `ModelConfig`s pointing at the local model router / Spark |
| `app/remotemcpserver-mcp-gateway.yaml` | the **single** `RemoteMCPServer` every agent uses (tools federated through the gateway) |
| `app/harness-kagent.yaml` / `workerpool-kagent-default.yaml` | the 1.0 Agentic-Tooling-Engine execution substrate |
| `app/rotator-cronjob.yaml` + `rotator-rbac.yaml` | mints/rotates the gateway bearer the agents present |
| `app/httproute*.yaml` / `securitypolicy.yaml` / `servicemonitor.yaml` / `prometheusrule.yaml` | ingress, auth, and observability |

Chart version **1.0.1** (pinned, CRDs + controller; Renovate bumps the
`OCIRepository` tags). Values verified against `helm show values …@1.0.0`.

## Posture

- **Local inference only.** Agents route through `litellm` (`ai/litellm`) to
  Ollama (P40) and vLLM (Spark) — no cloud provider keys, zero Claude tokens.
- **Tools via the federated gateway.** All 17 agents consume the single
  `mcp-gateway` `RemoteMCPServer`; there are no per-backend `RemoteMCPServer`s
  (that migration completed — see the migration doc above). Egress is granted in
  three layers (substrate `credentialProvider` namespace policies +
  `EgressPolicy` CRs + the `ai` → backend CNP).
- **No bundled database.** kagent 1.0 dropped the bundled dev Postgres; it takes
  external Postgres connection strings from Secrets.
- **No prebuilt-agent subcharts.** 1.0 dropped the `k8s-agent` /
  `observability-agent` / `promql-agent` / `cilium-*` / bundled `grafana-mcp`
  subcharts; the fleet is the in-repo `Agent` CRDs instead, and operator agents
  consume the shared `mcp-system/grafana-mcp` through the gateway.

See `app/values.yaml` for the full 1.0 schema notes (what changed vs the 0.10.x
trial) and the local-model wiring.

## Agent CRD content

The `Agent`/`AgentTemplate` CRDs here hold **method + wiring** (model config, tool
list, system-prompt shape) — the persona *method*, not private facts, which agents
fetch at runtime via tools. The canonical persona source is
`~/.claude-personal/agents/*.md`.

## Verify before merge

```sh
flux build ks kagent --path ./kubernetes/apps/ai/kagent/app   # or: kustomize build
helm template kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent --version 1.0.1 \
  -f kubernetes/apps/ai/kagent/app/values.yaml | less
```

After any infra/agent/content change, **prove the fleet works end to end** —
invoke a canary agent and confirm it reaches both the LLM and a tool
(`TASK_STATE_COMPLETED`); `ResolvedRefs`/`Ready`/flux-green alone do not prove it.
See `.agents/skills/kagent-e2e-verify.md`.

## Revert

Drop `./kagent/ks.yaml` from `kubernetes/apps/ai/kustomization.yaml` and delete this
directory → Flux prunes the controller, CRDs, and the agent fleet.
