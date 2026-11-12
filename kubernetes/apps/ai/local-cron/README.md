# local-cron — local-model (Ollama) cron tier

The scheduled, unattended cron tier of the local agent stack: **the
summarize / classify / triage / digest work that HOMELAB-SPEC Layer 6
assigns to local models**, so every run costs **zero Anthropic tokens**.
It complements the kagent fleet (`ai/kagent`) — kagent is the on-demand
agent runtime; this app is the time-triggered doer/triage/digest jobs.
(The old Claude-tier `ai/claude-runner` cron app this was once paired
with has been removed; local-cron stands on its own now.)

The load-bearing design points:

- **Local model, never Claude.** Summarization runs on the in-cluster
  Ollama on the P40 (`ollama.ai.svc.cluster.local:11434`, `/api/generate`,
  model `qwen2.5:7b`). There is **no `ANTHROPIC_API_KEY` and no
  `CLAUDE_CODE_OAUTH_TOKEN`** anywhere in this app.
- **No MCP broker, no k8s API.** The job reads log state read-only via the
  Loki HTTP API, summarizes on Ollama, and posts one themed HTML email via
  notify-mcp (`/send`). None of that touches the k8s API, so the SA has zero RBAC
  and `automountServiceAccountToken: false`. Future API needs get scoped
  read-only RBAC — never the MCP broker (the standing fleet rule for unattended loops).
- **Pure curl, no custom image.** The job runs `docker.io/alpine/k8s`
  (carries `curl` + `jq`), so there is no image to build/publish and no
  Gate-0 auth spike.
- **Private sink.** The digest reaches Rob as a themed HTML email via
  notify-mcp (`/send`, `mcp-system:8060`), which holds the relay
  recipient-lock — so it can carry namespace/app names a public GitHub
  artifact must not, and needs no credentials. (Migrated off Pushover
  2026-10-15, fleet-wide.)

## Workflows

18 CronJobs today (`cronjob-*.yaml`; the files are authoritative for exact
schedules). They fall into three groups:

**Operator triage / doer** — per-domain health surfacing and remediation
proposals on the local models (paired with the matching kagent operator
persona):

- `ml-triage` / `ml-doer`
- `network-triage`
- `observability-triage` / `observability-doer`
- `smart-home-triage` / `smart-home-doer`
- `storage-triage` / `storage-doer`

**Concierge digests** — scheduled per-persona life-admin jobs:

- `arnold-queue-check` — training queue check
- `cooter-recall` — NHTSA recall watch for the fleet · `cooter-reminder-digest`
- `crusher-review` — weekly health-trend review · `crusher-weight-sync` — daily
  PUMP→Medikeep weight reconcile
- `vila-warranty-digest` — property warranty/expiry surfacing
- `warren-plan-refresh` — finance plan refresh

**Infra** — cluster-wide housekeeping:

- `homeops-hygiene` — cluster hygiene surfacing
- `renovate-pr-digest` (`30 12 * * *`, daily) — **action-only (narrowed
  2026-10-21):** read the open Renovate PRs (unauthenticated GitHub REST — the
  repo is public) and email ONLY the subset **stuck > 7 days**, flagging likely
  major bumps. Silent (exit 0, no send) when nothing is stuck. The full queue
  lives on the GitHub Renovate dashboard; this surface only pushes what is
  waiting on Rob (see `feedback_digests_problems_only_no_allclear_roster`).

All post to the private notify-mcp email sink; outputs are
problems-and-next-step only, never all-clear rosters.

> `loki-error-skim` was retired 2026-10-17 (home-ops#15048): its substring
> ranking surfaced the chattiest apps rather than the erroring ones, so every
> digest read as noise, and it duplicated the `observability-operator` triage
> watcher. The in-cluster triage fleet now covers log/error surfacing.

## Why Loki first, PR digest second

Loki was the clean first wire: `auth_enabled: false`, monolithic
SingleBinary, one Service (`loki.observability:3100`), and a real LogQL
metric API that does the ranking server-side — a genuine ops-triage
digest with no extra credential.

The PR digest was originally deferred because a PR summary was assumed to
need a `gh` token. It doesn't: **home-ops is a public repo**, so the
Renovate-queue read is unauthenticated GitHub REST (60 req/hr unauth ≫ one
daily call) and rides the existing `world:443` egress — no token, no secret,
no CNP change. That removed the only reason it was held back, so it now ships
alongside the log skim.

## Network policy

The `ai` namespace is default-deny; `observability` is default-deny
ingress. Two holes were punched:

- **`ai/local-cron/app/cnp-allow.yaml`** (egress): `loki.observability:3100`,
  `notify-mcp.mcp-system:8060` (the email sink), plus `world:443`
  (`api.github.com` for the PR digest + `api.nhtsa.gov` for the cooter recall
  watch — external HTTPS). Ollama is **intra-ns** (both in `ai`), so it rides
  the baseline `allow-intra-namespace` — no rule needed.
- **`observability/loki/app/cnp-allow.yaml`** (ingress): added
  `ai/local-cron` as a cross-ns source on `loki:3100`. Loki previously had
  no ingress rules (only intra-ns Grafana/Vector via baseline), so this
  cross-ns caller had to be listed explicitly or it would silent-drop.

## Activation

**Status: ACTIVE as of 2026-08-23** — the ks and both CronJobs are
unsuspended and running on their schedules. To pause, re-add
`spec.suspend: true` to the CronJob (fast, keeps objects) or to the ks
(prunes the app's objects on next reconcile).

The original first-time activation order, for reference:

1. **Confirm the model is pullable/loadable.** `qwen2.5:7b` is already the
   khoj default and Open WebUI-selectable on this Ollama, so it is
   present. (If Ollama has been reset, `ollama pull qwen2.5:7b` on the
   P40.) No 1P work is required — the notify-mcp email sink is
   recipient-locked and needs no credentials.
2. **Unsuspend the ks** — remove `spec.suspend: true` from
   `local-cron/ks.yaml`. Flux then reconciles the ExternalSecrets
   (`kagent-doer` for the doer PR token, `cooter-recall` for the
   LubeLogger key), the CNP, the RBAC, and the CronJob object. The
   CronJob is still `suspend: true`, so nothing fires yet. Verify:
   `kubectl -n ai get externalsecret kagent-doer cooter-recall` shows `SecretSynced`.
3. **Test once, out of band.** With the CronJobs still suspended, test each:
   - `kubectl -n ai create job --from=cronjob/local-cron-renovate-pr-digest renovate-test`

   then `kubectl -n ai logs job/<name> -f`. Expect (for the digest), when
   something is stuck, `Stuck Renovate PRs (N):` + the list, then
   `digest emailed`. Nothing-stuck is success, not failure: the digest
   prints "No Renovate PRs stuck past 7d" — exit 0 and send nothing. (A
   healthy queue is now the common case, so the test often legitimately
   sends no email.)
4. **Unsuspend the CronJob(s)** — remove `spec.suspend: true` from
   `cronjob-renovate-pr-digest.yaml`. It then runs daily at 12:30 UTC.

To pause again: re-add `spec.suspend: true` to the CronJob (fast, keeps
objects) or to the ks (full teardown of the app's objects on next prune).

## Add a local-tier workflow

Copy `cronjob-renovate-pr-digest.yaml`, then:

- Keep the hardening verbatim (non-root 1000, `readOnlyRootFilesystem`,
  drop `ALL`, `RuntimeDefault`, tmpfs `/tmp`, `automountServiceAccountToken:
  false`, `k8tz.io/inject: "false"`, deadlines, `backoffLimit`, `Forbid`).
- Summarize on **Ollama**, never Claude — that is the whole point of this
  tier (HOMELAB-SPEC L6).
- Double every shell `$` as `$$` — Flux postBuild runs envsubst in strict
  mode over the manifest.
- Reach only what `cnp-allow.yaml` permits. New destination? Add a narrow
  egress rule here AND the matching ingress rule on the destination's own
  CNP (default-deny cuts both directions).
- Pick a private sink (notify-mcp email, `/send`) — never per-item spam.

## Kill criteria

Retire a workflow (delete its CronJob) if: useful-output rate < 30% after
2 weeks, OR zero acted-upon outputs in 14 days, OR > 5 noise digests in
any 7-day window. Local inference is free, so the bar is *usefulness of
the digest*, not token cost.
