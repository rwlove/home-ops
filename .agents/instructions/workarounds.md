# Workarounds

When this repo carries code that compensates for an upstream gap —
version pin, custom CNP, sidecar, disabled default — it must be
annotated so the upstream-watcher skill can retire it when upstream
catches up. Per HOMELAB-SPEC Layer 5 Workaround tracking.

## Annotation format

```
# workaround: <upstream-url> — remove when <condition>
```

- The `# workaround:` prefix is fixed. Don't invent variants (no
  `# WORKAROUND`, no `// workaround:`, no `# HACK:`).
- The upstream URL must point at a specific issue or PR, not a generic
  project page.
- The "remove when" condition should be checkable — a version, a
  merged PR, a behavior change. "Eventually" is not a condition.
- Example:

  ```
  # workaround: https://github.com/cloudnative-pg/cloudnative-pg/issues/5489 — remove when v1.27 lands
  ```

## When to apply

- Pinning to a known-broken version waiting for an upstream fix.
- Custom CNP / RBAC / config to compensate for a missing upstream
  feature.
- Sidecar containers that exist only to work around an upstream gap
  (e.g., `lidarr-sab-autoimport`).
- Disabling default behavior that's known-broken (e.g., gpu-operator
  NFD override for CRI-O nodes).

## GitHub label

Every workaround in code also has a tracking issue with the
`workaround` label:

- The label name is exactly `workaround` — do not invent variants.
- The issue links to the upstream issue/PR and the file(s) containing
  the annotation.
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
