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

## Verification discipline

"Upstream closed/merged" is necessary but NOT sufficient to remove a
workaround. Confirm the fix reaches *this cluster's actual state*, not a
stale reference in the annotation, before opening a removal PR.

- **Check the DEPLOYED version, not the version named in the comment.**
  Upstream may fix/re-index only the versions its issue mentions while
  the version we actually run still breaks. For the Fairwinds digest
  tolerance (2026-07-09), the comment named vpa 4.12.2 / goldilocks
  10.4.0 — both re-indexed clean — but the repo runs 4.12.3 / 10.4.1,
  which still mismatch. Removing the tolerance red the CI. Always
  `grep` the live `version:`/`tag:` in the HelmRelease and verify
  against THAT, not the annotation's example version.
- **"Issue closed" ≠ "fix is in the version we pin."** Find the closing
  PR, then confirm it shipped in a release `<=` our pinned version
  (`gh release list`, check the PR's merge date vs release dates).
- **For defects invisible to static rendering, verify against the live
  cluster.** Some workarounds compensate for behavior that `helm
  template` / flate never exercises (e.g. the ceph-csi `{}` padding —
  a `null` the Driver CRD only rejects on server-side apply). Removal
  is safe only if a real `helm template ... | kubectl apply
  --server-side --dry-run=server -f -` accepts it. Dry-run is
  read-only; nothing persists.
- **Let the removal PR's own CI be the final proof.** For CI-tolerance
  workarounds, the reverted wrapper runs against current versions in
  the PR — a green run is the confirmation, a red run means the
  workaround still earns its keep. Don't `--admin`-merge past it.

## What this is NOT

- Not a general bug-watcher — only tracks code we've explicitly
  annotated with `# workaround:` and labeled `workaround` in issues.
- Not a renovate replacement — version bumps are renovate's job. This
  is for behavior workarounds, not dep updates.
