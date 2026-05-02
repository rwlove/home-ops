---
name: pr-review
description: Apply this repo's PR review standards to a Renovate or manual PR
---

# PR Review

Apply these standards when reviewing a PR (Renovate or human-authored)
in this repo. Most of the substantive checks are GitOps and Helm
hygiene — Flux will reconcile whatever lands on `main`, so the review
is the only gate.

## Workflow

1. `gh pr view <num>` — read the title, description, base branch, and
   linked issues.
2. `gh pr diff <num>` — read the actual changes. Don't trust Renovate's
   summary alone; OCI digest changes can hide upstream behavior shifts.
3. For chart/image bumps, fetch release notes:
   - `gh release view <tag> --repo <owner>/<repo>` for GitHub releases.
   - For OCI digests, compare the source revision in the manifest's
     `org.opencontainers.image.revision` label.
4. Apply the checklist below.
5. `gh pr review <num> --approve` / `--request-changes` / `--comment`
   with a summary of what you verified and any concerns.

## HelmRelease requirements

- All applications use `HelmRelease` via Flux, not raw manifests.
- `chartRef` (preferred for app-template apps via the shared
  `OCIRepository/app-template`) **or** `chart.spec.version` pinned.
- `spec.interval` set.
- Resource requests/limits specified (limits SHOULD be present for
  production workloads but it's not a hard block).
- `valuesFrom` references ConfigMaps/Secrets — no inline secrets.

## Secret management

- **Never** commit plain-text secrets. If a PR adds a Secret, it must
  be backed by `ExternalSecret` referencing
  `ClusterSecretStore/onepassword-connect`.
- New ExternalSecret should set `creationPolicy: Owner` and a sane
  `refreshInterval`.

## Image & digest policy

- Prefer `tag@sha256:<digest>` over bare tags for reproducibility.
- For tag-only updates, verify the OCI metadata
  (`org.opencontainers.image.revision`, `.source`, `.created`).
- If revision changes between digests, ensure that's intentional
  (Renovate will sometimes pin a digest from a different branch).
- Reject updates from non-allowlisted registries. Preferred:
  `ghcr.io`, `registry.k8s.io`, Docker Hub (fallback only).
- Avoid Docker Hub for critical infrastructure components.

## Breaking-change triggers (auto request-changes)

- `apiVersion` change.
- Deprecated field newly introduced.
- Major version bump without justification in release notes.
- CRD schema change.
- Network policy or security context relaxation
  (e.g., `readOnlyRootFilesystem: true` → `false`,
  `runAsNonRoot: true` → unset, dropped capabilities re-added).

## Required evidence before approving

1. Release notes / changelog reviewed for the version range.
2. GitHub compare view shows expected file changes.
3. Reported version matches what Renovate said.
4. No breaking changes in the release notes for this version.
5. No security advisories apply.

## Repo-specific gotchas

- The `OCIRepository/app-template` is shared via
  `kubernetes/components/repos/app-template/`. A version bump there
  cascades to ~79 HelmReleases — review as a separate, careful PR.
- A `disable-<app>` or `Revert "disable-<app>"` commit is a manual
  Flux suspend/unsuspend marker. **Never** revert these as part of a
  general cleanup. See `skills/flux-suspend.md`.
- Renovate doesn't always rebuild lockfiles for `barmancloud.cnpg.io`,
  `gateway.envoyproxy.io`, and a few other newer CRDs — re-check if
  the PR claims a CRD bump.

## Cluster-specific posture

- Strict validation: every standard above is required, not advisory.

_Flux reconciles automatically once the PR merges — there's no
deploy-time safety net beyond this review._
