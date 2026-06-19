---
name: historian
description: Produce a weekly summary of this repo's work, written to the vault
---

# Historian

Produces vault summaries per HOMELAB-SPEC Layer 4 Historian + Layer 3
Documentation/Summaries.

## When to use

Manually, when rolling up the week. Daily/monthly/yearly will roll up
from weeklies once that's automated; only weekly is implemented today.

## Inputs

- `git log --since="1 week ago" --pretty=format:"%h %s"` — commits to
  main.
- `gh pr list --state merged --search "merged:>=$(date -d '7 days ago' +%Y-%m-%d)"`
  — PRs merged this week.
- Memory deltas: new files under
  `~/.claude-personal/projects/-home-rwlove-workspace-claude-workspace-home-ops/memory/`
  in the past week.
- Alert volume: query Prometheus for `ALERTS{alertstate="firing"}`
  deltas.
- Open work: `gh pr list --state open` + `gh issue list --label workaround`.

## Output

`~/vaults/claude/summaries/weekly/YYYY-WW.md` (ISO week number).

## Structure

- **Highlights** (3-5 bullets, top-of-mind).
- **Notable PRs** (merged this week; group by area).
- **Memory deltas** (what got learned/codified).
- **Alert weather** (deltas vs prior week).
- **Open / unresolved** (workarounds still in place, PRs still open).

## Data classification

- The vault is internal-tier. Memory and summary content is internal.
- But: the historian's summaries sometimes feed external surfaces
  (LinkedIn writeups, conference abstracts). Apply
  `.agents/instructions/data-classification.md` redaction *as if* the
  output might be external — better to redact at write time than scrub
  later.
- Restricted patterns from `data-classification.md` apply: never name
  the arr stack / stash; never emit secrets.

## What this is NOT

- Not a release-note generator — those belong in PR descriptions.
- Not a per-PR summary — operates at the week-scale.
- Not the canonical history — `git log` is. The summary is a digest.
