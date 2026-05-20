---
name: upstream-watcher
description: Re-check tracked workarounds against upstream and open removal PRs
---

# Upstream watcher

Per HOMELAB-SPEC Layer 4 Upstream-watcher + Layer 5 Workaround
tracking, periodically re-check that workarounds we've adopted are
still necessary upstream.

## When to use

Manually, ~weekly. Future: scheduled.

## Inputs

- `gh issue list --label workaround --state open` — tracking issues.
- `grep -rn '# workaround:' .` — annotated source (see
  `.agents/instructions/workarounds.md` for the format).
- For each tracked workaround: the upstream URL it points at.

## Workflow

1. List all open issues labeled `workaround` and gather their upstream
   URLs.
2. For each upstream URL:
   - Fetch via `gh issue view <upstream>` or `gh pr view <upstream>`
     (note: cross-repo for most).
   - If the upstream is closed/merged: collect for removal.
   - If still open: optionally post a status comment on the home-ops
     tracking issue.
3. For each removable workaround, open a PR that:
   - Removes the `# workaround:` annotation block.
   - Removes any compensating code (sidecar, custom CNP, version pin).
   - References the upstream resolution in the PR body.
   - Closes the home-ops tracking issue (`Closes #N`).
4. Use the sweep pattern from `CLAUDE.md` "Blast radius" if multiple
   workarounds retire at once (label `sweep`).

## What this is NOT

- Not a general bug-watcher — only tracks code we've explicitly
  annotated with `# workaround:` and labeled `workaround` in issues.
- Not a renovate replacement — version bumps are renovate's job. This
  is for behavior workarounds, not dep updates.
