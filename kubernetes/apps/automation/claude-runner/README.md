# claude-runner — cron-driven Claude Code workflows

Phase 4 scaffold for the [post-Spark roadmap](../../../../docs/src/audit/post-spark-tool-call-leakage-2026-05-18.md).

The Flux Kustomization ships with `spec.suspend: true` so the manifests
live in git as the staging point but Flux doesn't try to apply them
until the three gating items below are done.

## Gating: things that must land before unsuspending

### 1. Container image at `rwlove/containers`

Need a `claude-runner` image baked with the Claude Code CLI + an
opinionated MCP allowlist. Per `project_rwlove_containers_upstream_tarball`
this lives in the [`rwlove/containers`](https://github.com/rwlove/containers)
repo. Approximate Dockerfile:

```dockerfile
FROM node:20-alpine
RUN apk add --no-cache git curl jq && \
    npm install -g @anthropic-ai/claude-code

# Default per-workflow settings file — overridden by per-CronJob mount.
COPY settings.json /root/.claude/settings.json
COPY settings.json /workspace/.claude/settings.json

WORKDIR /workspace
ENTRYPOINT ["claude"]
```

The `settings.json` declares the MCP allowlist (gh, kubectl, loki, the
lovenet-gateway subset each workflow needs). Per-workflow overrides are
mounted via a ConfigMap at `/workspace/.claude/settings.json`.

Tag + push to `ghcr.io/rwlove/claude-runner:<sha>`; Renovate tracks.

### 2. 1Password items

Each workflow needs its own ExternalSecret. The shared 1P item
`claude-runner` (vault `Kubernetes`) should hold:

- `anthropic_api_key` — Anthropic API key for the Claude Code runtime
- `gh_token` — fine-grained PAT (repo:read for home-ops PRs, no write)
- `zulip_bot_token` — bot token for the `ops` stream
- `zulip_bot_email` — bot email
- `zulip_site` — `https://chat.${SECRET_DOMAIN}`

Plus per-workflow scope-narrowed alternates if needed (e.g., a separate
`gh_token` with broader perms for cost-cap workflows that read across
repos).

### 3. Unsuspend the Flux Kustomization

Edit `ks.yaml` to remove the `suspend: true` line. Commit + push. Flux
picks up the manifests on next reconcile, creates the namespace + RBAC
+ CronJobs.

## Workflows shipped in this scaffold

Two starter CronJobs per the Phase 4 plan recommendation (highest
signal, lowest noise):

| Workflow | Schedule (UTC) | What |
|---|---|---|
| `pr-triage` | `0 13 * * *` (= 09:00 EDT / 08:00 EST) | Read open PRs at rwlove/home-ops via gh MCP, summarize each Renovate change in one paragraph, post one Zulip card per PR to `ops/pr-triage`. |
| `cost-cap-commentary` | `0 22 * * *` (= 18:00 EDT / 17:00 EST) | Read `langgraph-agents /admin/costs/today` + Prometheus, project monthly Claude spend, post Zulip card. Flag if trending > $30/mo. |

Two more from the Outcomes table (`Daily HA log skim` + `Weekly Frigate
clip review`) are intentionally left for after a week of observation —
ship + watch the first two before adding more noise.

## Per-workflow kill criterion

Per Phase 4 plan body: kill any workflow if (a) useful-card rate < 30%
after 2 weeks, OR (b) zero acted-upon cards in 14 days, OR (c) > 5
unintended noise reactions in any 7-day window. Document the decision
in the plan's v-changelog and remove the CronJob from this dir.

## Container-image rebuild trigger

The image baked at step 1 above is a one-shot. To pick up a Claude Code
CLI bump or MCP-allowlist change, re-run the build at
`rwlove/containers` and bump the tag in each CronJob (or wait for
Renovate). The manifests here use the explicit tag for reproducibility.
