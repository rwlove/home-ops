# claude-runner routing — when & how to push work in

> **⚠ TRIAL POLICY — provisional.** Started **2026-08-22**; review **~2026-09-21**
> (30 days). The whole claude-runner automation approach is on probation. If it isn't
> earning its keep at the review (see *Trial & revert* below), revert it — one PR, Flux
> prunes everything, no residue.

`claude-runner` (ns `ai`) is a multi-lane automation surface — scheduled **Claude-tier
CronJobs**, a persistent **interactive shell** (`claude-runner-shell`), and a sibling
**local-model (Ollama) cron tier** (`local-cron`) — all drawing on the **one Max
subscription**. This file is the decision layer: *what runs where*. Two failure modes it
exists to prevent: the runner becoming a dumping ground for work that belongs elsewhere,
and unbounded Claude-tier crons burning the shared Max weekly limit that also feeds
interactive sessions.

## Execution lanes — pick one deliberately

| Lane | Use for | Cost |
|---|---|---|
| **Laptop `claude`** (nomad) | Exploratory, iterative, needs attention, short-lived | Max quota (interactive) |
| **In-cluster shell** (`claude-runner-shell`) | Long interactive tasks you kick off and walk away from; survives laptop sleep; has the vault + cluster-read context | Max quota (interactive) |
| **claude-runner CronJob** (Claude tier) | Recurring, unattended, reasoning / tool-use, read-mostly | Max quota (unattended) |
| **local-cron** (Ollama tier) | Summarize / classify / triage / log-skim | **Zero Anthropic tokens** |
| **Not automated** | One-off, destructive, or needs human judgment/approval | — |

## Push work into a claude-runner CronJob only when ALL hold

- **Recurring or event-triggered** — not a one-off (one-offs are interactive).
- **Genuinely needs Claude's reasoning or multi-step tool-use** — if it's really just
  summarize/classify/triage, it goes to **local-cron**, per HOMELAB-SPEC Layer 6.
- **Read-mostly / non-destructive** — its only "write" is a report to a sink. No cluster
  mutations, no destructive actions, *ever*, from an unattended loop.
- **Output has an appropriate sink** — private (Pushover) if it could carry internal or
  restricted-tier data; public (GitHub issue) only if the content is genuinely public.
- **Quota cost is justified** at a low cadence against the shared Max limit.

## Route ELSEWHERE when

- **Summarize / classify / triage / log-skim** → `local-cron` (Ollama). Default, per L6.
- **Exploratory / iterative / needs judgment** → interactive (laptop or shell).
- **Long but interactive-ish** (you want to watch/steer, just not stay tethered) → the shell.
- **Destructive / high-blast-radius / needs approval** → **not unattended**; interactive
  with Rob as the human gate.
- **Needs mutating cluster tools** (kubectl apply, HA/Omada control, …) → **not
  claude-runner**. No MCP broker for unattended loops — see
  `reference_mcp_broker_unsafe_for_unattended_loops` in memory. Do it interactively, or
  via a per-tool-authz'd path when that lands.

## How to add a Claude-tier workflow (recipe + guardrails)

1. Copy `kubernetes/apps/ai/claude-runner/app/cronjob-flux-longhorn-drift-digest.yaml`
   as the template.
2. **Prompt:** read-only source → reason → **one** sink message. Include the
   data-classification instruction (never secrets / media names / internal hostnames /
   restricted app names — the run's output is a narrative artifact, tier it).
3. **CNP:** add only the narrow read-only egress the workflow needs; add the app to the
   destination's **ingress allowlist** if it has one — Prometheus (`prometheus-allow`)
   and Loki both do, and a missing entry is a *silent* drop (exit 28 timeout). **Never
   the MCP broker.**
4. **Permissions:** explicit `--allowedTools` allowlist; **never**
   `--dangerously-skip-permissions`; cap `--max-turns`. **Ordering trap:**
   `--allowedTools` is variadic — put a single-value flag (`--max-turns`) *between* it
   and the positional prompt, or it swallows the prompt.
5. **Sink:** private (Pushover, reusing the 1P `Pushover` item) by default; public only
   for genuinely public content.
6. Keep the **hardening block verbatim** (non-root 1000, `readOnlyRootFilesystem`, drop
   `ALL`, `RuntimeDefault`, tmpfs work dirs, zero-RBAC SA,
   `automountServiceAccountToken: false`, `k8tz.io/inject: "false"`,
   `activeDeadlineSeconds`, `backoffLimit`, `concurrencyPolicy: Forbid`).
7. **Ship suspended** → manual test
   (`kubectl -n ai create job --from=cronjob/<name> <name>-test`) → verify the output →
   unsuspend.
8. **Cadence:** weekly by default; justify anything more frequent against quota.
9. **Kill criteria:** retire a workflow (delete its CronJob) if useful-output rate < 30%
   after 2 weeks, OR zero acted-upon output in 14 days, OR > 5 noise outputs in any 7-day
   window.
10. Emit a **success/last-run signal** so a silently-broken workflow is visible.

## Quota governance (the cross-cutting rule)

- Everything on claude-runner draws the **single shared Max subscription**, concurrently
  with interactive work. **Quota is the scarce resource, not compute.**
- Keep the **sum** of Claude-tier cron draw modest; leave clear headroom for interactive
  sessions. Prefer `local-cron` for anything that doesn't strictly need Claude.
- Under quota pressure, **Claude-tier crons throttle first** (raise intervals / suspend).
  Max has no clean meter, so the practical signal is: *are you hitting limits during
  interactive use?*

## Trial & revert

This whole approach is a **time-boxed trial** (header above), not a fixture.

**Keep** if: a Claude-tier workflow produces output that gets acted on; the runner never
starves interactive use; zero incidents attributable to it; the routing rules feel
natural. **Revert** if: quota pressure hurts interactive sessions; output is noise;
maintenance cost exceeds value; or it just isn't used.

**Revert = one PR** (clean, because it's GitOps + suspend-first):

1. Drop `./claude-runner/ks.yaml` + `./local-cron/ks.yaml` from
   `kubernetes/apps/ai/kustomization.yaml` and delete the two app dirs → Flux prunes the
   CronJobs, the shell, the vault-bridge, and the (ceph-block, regenerable) PVCs.
2. Revert the cross-ns CNP ingress additions (`prometheus-allow`, `loki`,
   `obsidian-couchdb-allow`) added for the runner.
3. Delete the `claude-runner` OAuth token **and the `github_issue_token`
   fine-grained PAT** from the 1Password `claude-runner` item.
4. **Leave intact** (independent of the trial): the Obsidian `claude` CouchDB db + vault
   sync, the Flatpak fix, the `containers` tmux image.

Reverting removes only the *in-cluster automation* — vault, sync, and laptop workflow are
untouched.

## What this is NOT

- Not a substitute for the app README (`kubernetes/apps/ai/claude-runner/README.md`),
  which owns app-specific mechanics (secret fields, image tag, current workflows). This
  file owns the *routing decision* + the reusable recipe.
- Not a permanent policy — it's on probation until the review date. Re-evaluate, don't
  treat as settled.
