# claude-runner — cron-driven Claude Code workflows

Phase 4 scaffold for the [post-Spark roadmap](../../../../docs/src/audit/post-spark-tool-call-leakage-2026-05-18.md).

## Activation status (2026-05-19)

| Gating item | State |
|---|---|
| 1. Container image at `rwlove/containers` | ✅ **DONE** — `ghcr.io/rwlove/claude-runner:0.1.1` (rwlove/containers PR #16 + #17) |
| 2. 1P item `claude-runner` in vault `Kubernetes` | 🟡 **PARTIAL** — created with `gh_token` + `zulip_bot_*` reused from existing items; `anthropic_api_key` is a **placeholder** the user must replace |
| 3. Unsuspend the Flux Kustomization | ⏳ pending (one-line `ks.yaml` edit) |

**To activate**: do step 2 (replace placeholder) + step 3 (remove the `spec.suspend: true` line). After Flux reconciles, the namespace gets RBAC + CronJobs + CNP and the next scheduled run fires.

## Step 2 detail — fill the placeholder

The 1P item `claude-runner` exists with all five expected fields:

- `anthropic_api_key` — **PLACEHOLDER** (`sk-ant-PLACEHOLDER-REPLACE-WITH-REAL-KEY`). Generate from <https://console.anthropic.com/settings/keys> and replace via `op item edit "claude-runner" --vault Kubernetes anthropic_api_key=sk-ant-…` (or via the 1P desktop app).
- `gh_token` — **REUSED** from `github` item's `github_notification_token` field. Long-term, rotate to a dedicated PAT scoped to the home-ops repo (PR read only).
- `zulip_bot_email` — **REUSED** from `zulip-bots-langgraph.ZULIP_REPORTER_EMAIL` (the `reporter` bot).
- `zulip_bot_token` — **REUSED** from `zulip-bots-langgraph.ZULIP_REPORTER_API_KEY`.
- `zulip_site` — `https://chat.thesteamedcrab.com`.

**Implications of reusing the reporter bot**: cron-runner Zulip messages will appear under the same bot identity as the langgraph reporter agent (when that activates). For Phase 4 alone this is fine — both are "report on the world" workflows. If you want a dedicated `claude-runner` bot, create it in the Zulip admin UI and update the 1P fields.

## Step 3 — unsuspend

Edit `ks.yaml`, drop the `suspend: true` line, commit, push. The next Flux source-controller poll (≤1 min) pulls the change; helm-controller / kustomize-controller reconciles within the HR `interval: 30m` OR sooner via `flux reconcile kustomization claude-runner -n automation --force`.

## Workflows shipped

| Workflow | Schedule (UTC) | What |
|---|---|---|
| `pr-triage` | `0 13 * * *` (= 09:00 EDT / 08:00 EST) | Read open PRs at rwlove/home-ops via gh MCP, summarize each Renovate change in one paragraph, post one Zulip card per PR to `ops/pr-triage`. |
| `cost-cap-commentary` | `0 22 * * *` (= 18:00 EDT / 17:00 EST) | Read `langgraph-agents /admin/costs/today` + Prometheus, project monthly Claude spend, post Zulip card. Flag if trending > $30/mo. |

Two more from the Outcomes table (`Daily HA log skim` + `Weekly Frigate clip review`) are intentionally left for after a week of observation — ship + watch these two before adding more noise.

## Kill criterion (per Phase 4 plan body)

Kill any workflow if:
- useful-card rate < 30% after 2 weeks, OR
- zero acted-upon cards in 14 days, OR
- > 5 unintended noise reactions in any 7-day window.

Document the kill in the plan's v-changelog and remove the CronJob from this dir.

## Container-image rebuild

The image at `rwlove/containers/claude-runner` is tag-driven. To pick up a Claude Code CLI bump or MCP-allowlist change, edit the `CLAUDE_CODE_VERSION` ARG in `claude-runner/Dockerfile`, push, tag `claude-runner-v<new>`, then bump the image tag in each CronJob here (or wait for Renovate).
