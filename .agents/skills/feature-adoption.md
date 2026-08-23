---
name: feature-adoption
description: Turn an adoption-scout feature candidate into a verified, pre-submitted PR
---

# Feature adoption

The interactive back-end for the **feature** half of the `adoption-scout`
digest (the workaround half is `upstream-watcher.md`). The scout only reports
candidates in a public GitHub issue; this skill turns one into a PR.

## When to use

When a digest line (or your own reading of a chart/image bump) proposes a new
upstream feature home-ops should adopt, and you want to author the change.

## Inputs

- An `adoption-scout` issue candidate: app, deployed version, the feature, the
  proposed next action (file / values key).
- The deployed version of the chart/image (from the HelmRelease /
  OCIRepository — Git is canonical).

## Workflow

1. **Re-verify against the DEPLOYED version.** This is the load-bearing step —
   release-note research skews optimistic, and roughly half of candidates
   evaporate here (see `feedback_verify_adoptable_features_against_deployed_version`
   in memory). Confirm the feature/option actually exists in the version we
   pin — not upstream main — by checking that version's chart `values.yaml` /
   CRD / release notes. If it isn't there yet, stop and note the version it
   lands in.
2. **Confirm it fits our architecture.** A feature that assumes a component,
   runtime, or topology we don't run is not adoptable. Check the relevant
   per-domain instruction file (`storage-class`, `helmrelease.security`,
   `gpu-routing`, …) before proposing the change.
3. **Make the change** in a dated worktree per `CLAUDE.md` "Worktree
   isolation". Keep it small and reviewable; one feature per PR. If it retires a
   `# workaround:`, fold in the annotation removal and `Closes #N` the tracking
   issue (that's the `upstream-watcher` overlap).
4. **Run pre-submit** (`.agents/skills/pre-submit.md`) — kustomize build, schema
   validation, sorting. Per HOMELAB-SPEC Layer 2 #6 the PR is the artifact of a
   passing local run.
5. **Open the PR.** Respect the 50-file blast-radius limit; scrub restricted
   app names / media names / hostnames from the title and body
   (`data-classification.md`).

## What this is NOT

- Not automated — the `adoption-scout` cron deliberately does not open PRs. The
  ~50%-optimism rate and the pre-submit invariant make human authorship the
  gate.
- Not a version-bump tool — Renovate owns bumps. This adopts a *feature* a bump
  made available.
