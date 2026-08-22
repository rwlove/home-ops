# local-cron — local-model (Ollama) cron tier

Phase 2 of the two-tier cron routing established by `ai/claude-runner`.
Where `claude-runner` hosts workflows that genuinely need Claude's
reasoning + tool-use, **this app hosts the summarize / classify /
log-triage work that HOMELAB-SPEC Layer 6 assigns to local models** — so
those runs cost **zero Anthropic tokens**.

The load-bearing design points:

- **Local model, never Claude.** Summarization runs on the in-cluster
  Ollama on the P40 (`ollama.ai.svc.cluster.local:11434`, `/api/generate`,
  model `qwen2.5:7b`). There is **no `ANTHROPIC_API_KEY` and no
  `CLAUDE_CODE_OAUTH_TOKEN`** anywhere in this app.
- **No MCP broker, no k8s API.** The job reads log state read-only via the
  Loki HTTP API, summarizes on Ollama, and posts one Pushover message.
  None of that touches the k8s API, so the ServiceAccount has zero RBAC
  and `automountServiceAccountToken: false`. Future API needs get scoped
  read-only RBAC — never the broker (same rule as `claude-runner`).
- **Pure curl, no custom image.** The job runs `docker.io/alpine/k8s`
  (carries `curl` + `jq`), so there is no image to build/publish and no
  Gate-0 auth spike — unlike `claude-runner`.
- **Private sink.** The digest goes to Pushover (the same 1P `Pushover`
  item alertmanager uses), so it can carry namespace/app names a public
  GitHub artifact must not.

## Workflows

| Workflow | Schedule (UTC) | Tier | What |
|---|---|---|---|
| `loki-error-skim` | `0 12 * * *` (08:00 EDT / 07:00 EST, daily) | Local (Ollama) | LogQL-rank the top error/panic/fatal log producers in Loki over 24h, pull a small sample of lines from the top 5, have the local model distill them into a terse pattern digest, send ONE Pushover message. Silent (exit 0, no push) when there are no error lines. |

Query path: `topk(15, sum by (namespace, app) (count_over_time({namespace=~".+"} |~ "(?i)(error|panic|fatal|traceback|exception)" [24h])))`
against `loki:3100` (Loki stream labels here are `app` / `namespace` /
`node`, set by the Vector aggregator sink).

## Why Loki (not the PR-summary fallback)

Loki was the clean wire: `auth_enabled: false`, monolithic SingleBinary,
one Service (`loki.observability:3100`), and a real LogQL metric API that
does the ranking server-side. The PR-summary fallback would have needed a
`gh` token + world egress to `api.github.com` for a weaker signal. Loki
gives a genuine ops-triage digest with no extra credential.

## Network policy

The `ai` namespace is default-deny; `observability` is default-deny
ingress. Two holes were punched:

- **`ai/local-cron/app/cnp-allow.yaml`** (egress): `loki.observability:3100`
  plus `world:443` (Pushover). Ollama is **intra-ns** (both in `ai`), so it
  rides the baseline `allow-intra-namespace` — no rule needed.
- **`observability/loki/app/cnp-allow.yaml`** (ingress): added
  `ai/local-cron` as a cross-ns source on `loki:3100`. Loki previously had
  no ingress rules (only intra-ns Grafana/Vector via baseline), so this
  cross-ns caller had to be listed explicitly or it would silent-drop.

## Activation (shipped suspended)

Both the Flux `ks.yaml` and the CronJob ship `suspend: true`. Activate in
order:

1. **Confirm the model is pullable/loadable.** `qwen2.5:7b` is already the
   khoj default and Open WebUI-selectable on this Ollama, so it is
   present. (If Ollama has been reset, `ollama pull qwen2.5:7b` on the
   P40.) No 1P work is required — the `Pushover` item already exists and
   is shared with alertmanager.
2. **Unsuspend the ks** — remove `spec.suspend: true` from
   `local-cron/ks.yaml`. Flux then reconciles the ExternalSecret
   (`local-cron-secret`), the CNP, the RBAC, and the CronJob object. The
   CronJob is still `suspend: true`, so nothing fires yet. Verify:
   `kubectl -n ai get externalsecret local-cron` shows `SecretSynced`.
3. **Test once, out of band.** With the CronJob still suspended:
   `kubectl -n ai create job --from=cronjob/local-cron-loki-error-skim skim-test`
   then `kubectl -n ai logs job/skim-test -f`. Expect the ranked list, the
   `----- local-model digest -----` block, and `Pushover digest sent`. If
   Loki returns no error lines it exits 0 with "Nothing to report" and
   sends nothing — that is success, not failure.
4. **Unsuspend the CronJob** — remove `spec.suspend: true` from
   `cronjob-loki-error-skim.yaml`. It now runs daily at 12:00 UTC.

To pause again: re-add `spec.suspend: true` to the CronJob (fast, keeps
objects) or to the ks (full teardown of the app's objects on next prune).

## Add a local-tier workflow

Copy `cronjob-loki-error-skim.yaml`, then:

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
- Pick a private sink (Pushover) — never per-item spam.

## Kill criteria

Retire a workflow (delete its CronJob) if: useful-output rate < 30% after
2 weeks, OR zero acted-upon outputs in 14 days, OR > 5 noise digests in
any 7-day window. Local inference is free, so the bar is *usefulness of
the digest*, not token cost.
