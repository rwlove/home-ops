# Goal: HomeAIOps to Production-Ready, Then Invert the Workflow

## Context

`CLAUDE.md` is the canonical context for this work. Read it first. It defines
the architecture, the pipeline inputs, the workflows, what "working" means, and
the DoD references this prompt points at.

Conflict resolution order:

- If this prompt and `CLAUDE.md` disagree on **facts about the system**,
  `CLAUDE.md` wins.
- If this prompt and `CLAUDE.md` disagree on **stage scoping or rules of
  engagement**, this prompt wins.
- If the **repo** and `CLAUDE.md` disagree, the repo wins. Flag the drift and
  update `CLAUDE.md` as part of the relevant PR.

Also read existing `TODO.md`, prior memories, and inline `TODO(homeaiops):`
comments. Treat them as authoritative state on what's done and what's pending.

## Why this run exists

We've been going two steps forward, one back. The point of this run is to break
that pattern. Verify before you declare done. Keep state honest as you go.
Don't skip the gates between stages.

## Rules of engagement (apply to all stages)

- One PR per logical concern. Max 3 PRs open concurrently.
- PR → wait for green CI → rebase on main if needed → merge.
- No opportunistic refactors, dependency bumps, or scope creep. If you find
  something out of scope that's broken, file an issue, don't fix it.
- Bug triage: P0 (blocks current stage DoD) fix now. P1 file an issue and
  continue. P2 add to `TODO.md`.
- If CI is red and the cause isn't obvious flakiness, stop and tell me.
- Update `TODO.md`, memories, inline `TODO`s, and `CLAUDE.md` as you close
  items. A rich context doc that's drifted from reality is worse than a sparse
  one — treat `CLAUDE.md` maintenance as part of the work, not an afterthought.
- For each verification, paste the actual output in the PR. No "looks good" —
  show the green.
- Parallelize aggressively *within* a stage (independent components,
  independent PRs). Do not parallelize *across* stages — the gates exist
  because scaffolding a later stage while an earlier stage is flaky is the
  exact pattern we're breaking.
- When in doubt about scope, intent, or whether something counts as "done" —
  ask. A two-line clarification beats a two-day detour.

---

## Stage 1 — Stabilize

Audit every HomeAIOps component top-to-bottom against the DoD documented in
`CLAUDE.md` and the repo. For each component: run the documented verification,
paste evidence in the PR, fix what's broken.

### Definition of done

- [ ] All HomeAIOps components pass their documented health checks
- [ ] End-to-end smoke test passes: task in → result out
- [ ] Survives a `flux suspend` / `resume` cycle on the relevant kustomizations
      without manual intervention
- [ ] Survives a pod-level restart of each component
- [ ] Runbooks exist in `docs/runbooks/` for the top failure modes hit while
      stabilizing

### Gate 1

Stop here. Post the smoke test output and the runbook list. Wait for my
approval before starting Stage 2.

---

## Stage 2 — Migrate workflows onto HomeAIOps

### Capability gap analysis (do this first, before building)

Produce a written gap analysis covering:

- What my current Claude Code workflow provides (todos, task dispatch, session
  history, anything else surfaced in memory or `CLAUDE.md`)
- What HomeAIOps provides today
- The gap, prioritized

The CLI is **my** primary interface to the pipeline, replacing my current
Claude Code workflow. It is **not** the pipeline's only input — the repo
already defines other inputs that feed the same task store and execution path.
Treat the CLI as a peer to those, not a replacement for them. Shared concerns
(task schema, store, dispatch, result handling) belong in the pipeline; the
CLI is a thin client on top.

In the gap analysis, confirm: which existing inputs feed the pipeline today,
what schema/contract they use, and where the CLI plugs into that same
contract. If the contract is implicit or inconsistent across inputs, making
it explicit is part of Stage 2 — but as a deliberate sub-task, not a drive-by
refactor.

Show me the gap analysis before you start closing it.

### Then close the gaps

- Build/finish the terminal CLI that submits tasks to the local AI pipeline.
  Installed on `$PATH`, `--help` works, core commands documented.
- Move todo management out of Claude Code into HomeAIOps' own store. Claude
  Code becomes a consumer, not the store.
- Each capability moves in its own PR with a documented rollback path.

### Definition of done

- [ ] CLI is the primary interface for submitting tasks to local AI
- [ ] Todos live in HomeAIOps; Claude Code is a consumer, not the store
- [ ] Existing non-CLI inputs still work end-to-end (verified, not assumed)
      after the CLI lands
- [ ] One full day of dogfooding without falling back to Claude Code for the
      primary task flow

### Gate 2

Stop here. Post the day-of-dogfooding log. Wait for my approval before
starting Stage 3.

---

## Stage 3 — Invert: local-first with Claude escalation

Today I drive Claude Code, and Claude sometimes calls local infra. Target:
I drive HomeAIOps, and HomeAIOps decides when to escalate to Claude.

Routing, escalation, provenance, and cost visibility apply to **all** inputs
into the pipeline, not just CLI-submitted tasks. The routing policy is
input-agnostic: a task is a task regardless of whether it arrived via CLI or
any other input defined in the repo. Provenance records the input source
alongside the execution target so I can see, per input, what's running local
vs. escalating.

### Required pieces

- **Routing policy.** Version-controlled file in the repo that decides
  local vs. escalate based on explicit rules (task type, model capability,
  context size, sensitivity). No learned router yet — explicit rules first.
  Changing the file changes behavior.
- **Escalation client.** HomeAIOps calls Claude as a subordinate worker. Use
  Claude Code in headless mode for tool-using/coding tasks; use the raw
  Anthropic API for classification, summarization, drafting. Document the
  rule for which is used when.
- **Provenance.** Every task result records where it ran (local model +
  version, or Claude + model string) and which input it arrived from.
- **Fallback on failure.** If local execution fails or returns low
  confidence, auto-escalate with the failure context attached — don't restart
  from a fresh prompt.
- **Cost/usage visibility.** CLI subcommand showing local vs. escalated task
  counts and Claude spend over the last N days. Without this, "local-first"
  silently becomes "Claude-first with extra steps."
- **Guardrail against silent Claude-creep.** Budget threshold; if escalation
  rate or spend crosses it, surface a warning. (This is the failure mode I'm
  most worried about — drift toward escalating everything.)

### Definition of done

- [ ] I issue tasks to the CLI by default for one full week (wall-clock; the
      week ends when I confirm it, not when you think the work is done)
- [ ] At least one task type round-trips local → escalate → result with no
      intervention from me
- [ ] Routing policy file is the single source of truth; behavior changes when
      I edit it
- [ ] Escalation and provenance work uniformly across all pipeline inputs, not
      just CLI
- [ ] Escalation rate and Claude spend are visible from the CLI
- [ ] Runbook exists for "local infra is down, fall back to Claude Code
      directly"

---

## Final note

If something in this prompt contradicts what you find in `CLAUDE.md` or the
repo, apply the conflict resolution order at the top and tell me what you
found. Don't paper over it.
