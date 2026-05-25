---
name: flux-suspend
description: Reference for the disable-<app> manual Flux suspend pattern in this repo
---

# Flux Suspend / Resume

This repo uses a deliberate manual-suspend pattern when an app needs to
go offline temporarily — for hand-edits during a release, breaking-glass
maintenance, or to keep Flux from clobbering an in-flight workaround.

If you see this in `git log`, **do not "fix" it**:

```text
abc1234 new: disable-<app>          # commit that pauses reconciliation
def5678 Revert "disable-<app>"      # commit that unpauses
```

Without explicit user instruction, never:

- Revert a `disable-<app>` commit.
- Run `flux resume` on a suspended Kustomization or HelmRelease.
- Edit `spec.suspend` in a manifest.
- "Clean up" what looks like out-of-sync state on a suspended app.

If you spot an unexpected `Suspended: True`, **ask first**.

## Suspending (when explicitly asked)

The repo's convention is to suspend by committing the change rather
than running `flux suspend` imperatively, so that the suspended state
is in Git and survives reconciliations.

For an app's HelmRelease, set `spec.suspend: true` (or comment out the
ks.yaml entry in the parent `kustomization.yaml`) on a branch named
`disable-<app>` and commit with a message like:

```text
new: disable-<app>

<reason — e.g. "manual hand-edit during X migration">
```

For imperative-only emergencies:

```sh
flux suspend kustomization <name> -n flux-system
flux suspend helmrelease <name> -n <namespace>
```

But follow up by committing the suspend so it's tracked in Git.

## Resuming

Revert the suspend commit:

```sh
git revert <disable-commit-sha>
```

The commit message should be left as the default
`Revert "disable-<app>"` — that's the searchable pattern.

For imperative-only resume:

```sh
flux resume kustomization <name> -n flux-system
flux resume helmrelease <name> -n <namespace>
```

## Inspecting suspend state

```sh
# Everything that's currently suspended
flux get all -A --status-selector ready=false | grep -i suspend

# A specific app
kubectl get hr <name> -n <namespace> -o jsonpath='{.spec.suspend}'
kubectl get ks <name> -n flux-system -o jsonpath='{.spec.suspend}'
```

## Why the pattern matters

The user explicitly documented this in `CLAUDE.md` because earlier
agents reverted disable commits during "cleanup" sweeps. The
`disable-<app>` / `Revert "disable-<app>"` shape is intentionally
greppable so a quick `git log --oneline | grep disable-` shows the
history of every manual pause.

See also: `CLAUDE.md` § "Flux suspend / disable workflow".
