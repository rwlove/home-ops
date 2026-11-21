# Workarounds

When this repo carries code that compensates for an upstream gap —
version pin, custom CNP, sidecar, disabled default — it must be
annotated so the upstream-watcher skill can retire it when upstream
catches up. Per HOMELAB-SPEC Layer 5 Workaround tracking.

## Annotation format

```text
# workaround: <upstream-url> — remove when <condition>
```

- The `# workaround:` prefix is fixed. Don't invent variants (no
  `# WORKAROUND`, no `// workaround:`, no `# HACK:`).
- The upstream URL must point at a specific issue or PR, not a generic
  project page.
- The "remove when" condition should be checkable — a version, a
  merged PR, a behavior change. "Eventually" is not a condition.
- Example:

  ```text
  # workaround: https://github.com/cloudnative-pg/cloudnative-pg/issues/5489 — remove when v1.27 lands
  ```

## When to apply

- Pinning to a known-broken version waiting for an upstream fix.
- Custom CNP / RBAC / config to compensate for a missing upstream
  feature.
- Sidecar containers that exist only to work around an upstream gap
  (e.g., a sidecar that polls app state and triggers scans the app
  doesn't trigger itself).
- Disabling default behavior that's known-broken (e.g., gpu-operator
  NFD override for CRI-O nodes).

## Upstream filings are always tracked

Every issue or PR we file in a third-party / upstream project gets a
tracking issue here, opened in the same session as the filing (standing
rule — applies to every agent and persona). This holds even when nothing in
this repo compensates for the gap: an adoption we're holding back, a bug we
only avoid by convention, or a fix we're contributing upstream.

- Same label, `Watch:` line, and retire flow as below, so the
  `workaround-watcher` flags it when upstream resolves.
- When there's no in-code annotation, the issue body says so ("nothing in
  code; adoption gate" / "avoid X until fixed") instead of linking a file.
- One tracking issue per upstream problem. An upstream issue and the PR
  that fixes it share one: link both, and `Watch:` the item whose
  resolution retires it.
- Body sections: *Upstream*, *Impact here*, *Until fixed*, *Retire when*.
  Examples: #16250, #16251.
- This repo is public: run the data-classification check before posting.

## GitHub label

Every workaround in code also has a tracking issue with the
`workaround` label:

- The label name is exactly `workaround` — do not invent variants.
- The issue links to the upstream issue/PR and the file(s) containing
  the annotation.
- The issue body carries one machine-readable gate line for the Windmill
  `workaround-watcher`: `Watch: <github issue/PR URL>` — the single item
  whose resolution retires the workaround — or `Watch: none` when no
  upstream item gates it (no upstream issue exists, or the exit is a local
  migration). Without it the watcher falls back to an `Upstream: <url>`
  line, then the first GitHub link under an `## …Upstream…` heading, which
  misfires on bodies that cite a closed "closest match". An upstream issue
  closed NOT_PLANNED is never treated as retireable — re-point `Watch:`.
- One issue per upstream item, not per occurrence. One upstream bug
  touching three files → one `workaround`-labeled issue.

## How to retire

- When upstream resolves: file a PR that removes the workaround code
  and references the upstream resolution.
- Close the `workaround`-labeled tracking issue from the PR
  (`Closes #N`).
- This is what `.agents/skills/upstream-watcher.md` exists to find.

## What this is NOT

- Bug fixes against this repo's own code are not workarounds — fix
  them directly.
- Technical debt with no upstream gate is not a workaround — use
  `# TODO:` instead.
- A workaround needs an explicit upstream link. If you can't link,
  you don't have a workaround — you have an unjustified hack.

Pairs with the `upstream-watcher` skill, which scans
`workaround`-labeled issues weekly.
