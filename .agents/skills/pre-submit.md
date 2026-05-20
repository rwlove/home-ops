---
name: pre-submit
description: Pre-submit checklist for agent-authored PRs in this repo
---

# Pre-submit

Agents do not open PRs that haven't passed local pre-submit
(HOMELAB-SPEC Layer 2 #6). This is the author-time gate.

## When to use

Before pushing a branch and opening a PR. Pairs with
`.agents/skills/pr-review.md` (the matching review-time gate run by an
independent agent).

## Checks

1. **Flux render diff** — run `flux-local diff` (or the repo's
   wrapper) against `main` and verify the rendered manifest changes
   match what you expect. Unexpected diffs usually mean an upstream
   chart bumped under you.
2. **YAML schema validation** — every YAML in `kubernetes/` should
   have the right `# yaml-language-server: $schema=` comment on line
   2 per `.agents/instructions/schema.correction.md`. Open each
   new/changed file and confirm.
3. **Image pull check** — for any new image reference, confirm it
   pulls (or is set to sync via ZOT). `crane manifest <ref>` is one
   way.
4. **Lint** — match whatever CI runs (markdownlint, yamllint,
   `kustomize build`).
5. **File-count** — see `CLAUDE.md` "Blast radius". 50-file ceiling;
   `sweep` label required to bypass. If you're above 50 and not
   labeling `sweep`, split.
6. **Memory grep** — search
   `~/.claude-personal/projects/-home-rwlove-workspace-claude-workspace-home-ops/memory/`
   for any prior decisions that contradict the change. Cross-namespace
   grep too (per global `CLAUDE.md` "Cross-namespace memory").
7. **Data classification** — review the diff and PR description per
   `.agents/instructions/data-classification.md`. Refuse to emit
   restricted content to external surfaces.

## Why

HOMELAB-SPEC Layer 2 #6: "The PR is the artifact of a passing local
run."

## What this is NOT

- Not a substitute for CI. CI still runs post-PR.
- Not a code review — see `.agents/skills/pr-review.md` for that.
- Not a strict checklist that blocks every PR — judgment call when the
  changes are obviously safe (renovate digest bumps, doc-only PRs).
  Document the skip reason in the PR body.
