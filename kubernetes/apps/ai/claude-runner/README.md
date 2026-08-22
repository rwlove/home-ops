# claude-runner — cron-driven Claude Code workflows (subscription)

A revival of the retired `automation/claude-runner`, re-homed to the `ai`
namespace and updated for **subscription auth** (never the API). It runs
headless `claude --print` workflows on a schedule so "check for events
occasionally and act" work doesn't depend on a laptop.

Design decisions live in the plan (`moonlit-brewing-dijkstra`). The load-bearing
ones:

- **Subscription only, never the API.** Auth is `CLAUDE_CODE_OAUTH_TOKEN` from
  `claude setup-token` (rides Max). No `ANTHROPIC_API_KEY` anywhere.
- **No MCP broker.** The Kuadrant broker has no enforceable per-tool authz and
  fronts mutating backends; its own note says do not point an unattended loop
  at it. This runner reads cluster state the enforceable way — **read-only
  Prometheus** (`cnp-allow.yaml`). Future API needs get scoped read-only RBAC,
  never the broker.
- **Two-tier routing.** Summarize / classify / triage / log-triage belong on
  **local models** (`sgpt`), per HOMELAB-SPEC Layer 6 — not here. This runner
  hosts only workflows that need Claude's reasoning + tool-use.

## Activation (shipped suspended — Gate 0)

The Flux `ks.yaml` ships `suspend: true`. Activate in order:

1. **Token.** On nomad: `claude setup-token` → copy the value →
   `op item edit "claude-runner" --vault Kubernetes claude_code_oauth_token=<token>`.
   Subscription token only — never an API key. (Pushover creds are pulled from
   the separate 1P `Pushover` item; no other fields needed on `claude-runner`.)
2. **Image.** Confirm `ghcr.io/rwlove/claude-runner` is published and current
   (plan D3: add `tmux`, bump `CLAUDE_CODE_VERSION`); set the tag in the app
   manifests. Ships suspended, so nothing pulls until step 3.
3. **Unsuspend the ks** (remove `spec.suspend`). Flux creates the
   `claude-runner-secret` ExternalSecret and runs the **auth-spike Job**.
   Confirm: `kubectl -n ai logs job/claude-runner-auth-spike` prints
   `SUBSCRIPTION-HEADLESS-OK`. **If it fails or demands an API key, stop** —
   the subscription-headless assumption is void; re-decide per the plan.
4. **Unsuspend the CronJob** (remove `spec.suspend` from
   `cronjob-flux-longhorn-drift-digest.yaml`). Test once with
   `kubectl -n ai create job --from=cronjob/claude-runner-flux-longhorn-drift-digest drift-test`.

## Workflows

| Workflow | Schedule (UTC) | Tier | What |
|---|---|---|---|
| `flux-longhorn-drift-digest` | `0 13 * * 1` (Mon 09:00 EDT) | Claude | Read Flux/Longhorn health from Prometheus, reason about real drift + a fix, send ONE Pushover digest (private — reuses the alertmanager Pushover app). Silent when clean. |

Summarize/triage-style checks (Renovate-PR summaries, log skims, doc-drift)
do **not** go here — they are the local-model (`sgpt`) tier per HOMELAB-SPEC
Layer 6.

## Interactive shell (survives laptop sleep)

`deployment-shell.yaml` runs a persistent `claude-runner-shell` Deployment that
holds a detached `tmux` session on the subscription token. Use it to kick off a
long interactive task and walk away — the session keeps running in-cluster when
your laptop sleeps.

```sh
kubectl -n ai exec -it deploy/claude-runner-shell -- tmux attach -t main
# then inside: claude
# detach: Ctrl-b d   (session + any running task persist; reattach anytime)
```

HOME (`/home/node`) is a `ceph-block` PVC (`claude-shell-data`), so Claude
transcripts + scratch clones survive pod restarts. Needs the tmux-bearing image
(`ghcr.io/rwlove/claude-runner:v2.1.240+`, built with `tmux` in the `containers`
repo). Egress is the same CNP as the cron workflows (read-only Prometheus +
world:443); attach is via `kubectl exec`, no route.

## Add a Claude-tier workflow

Copy `cronjob-flux-longhorn-drift-digest.yaml`, then:

- Keep the hardening block verbatim (non-root 1000, `readOnlyRootFilesystem`,
  drop `ALL`, `RuntimeDefault`, tmpfs work dirs, `automountServiceAccountToken:
  false`, `k8tz.io/inject: "false"`, deadlines, `backoffLimit`, `Forbid`).
- Reach only what `cnp-allow.yaml` permits (Prometheus + world:443). Need more?
  Add a **narrow** egress rule + scoped read-only RBAC — never the MCP broker.
- Set `--allowedTools` explicitly; never `--dangerously-skip-permissions`.
- Pick a sink: a single GitHub issue/comment, or Pushover — never per-PR spam.
- Keep the cadence low (shared Max limits).

## Kill criteria

Retire any workflow (delete its CronJob) if: useful-output rate < 30% after
2 weeks, OR zero acted-upon outputs in 14 days, OR > 5 noise outputs in any
7-day window.
