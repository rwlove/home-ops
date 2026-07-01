# Repo audit — 2026-07-01

Full-repo review across three dimensions — security posture,
simplification, optimization — covering ~200 HelmReleases, 190 Flux
Kustomizations, and 13 CI workflows. Three parallel audits, findings
verified against the tree before acting.

## Overall posture

Above-average for the repo class, before any fixes:

- All GitHub Actions SHA-pinned; no `pull_request_target`; top-level
  `permissions: contents: read` everywhere.
- gitleaks (PR diff + push scans), CodeQL, and a data-classification
  scrub in CI.
- No plaintext secrets in the tree — every credential resolves through
  an ExternalSecret.
- Only one `cluster-admin` reference, and it is a *downgrade* patch
  (longhorn support-bundle → `view`).
- Phased NetworkPolicy rollout with audit soak, 176 CiliumNetworkPolicies.
- Zero CPU limits (no throttling); healthy Flux reconcile intervals
  (nothing below 5m, almost everything 30m–1h).

## Fixed immediately (Phase A)

| Fix | PR |
|---|---|
| metrics-server ran `privileged: true` + `allowPrivilegeEscalation` + `NET_ADMIN`/`NET_RAW` — 2022-era debug leftovers. Now on the hardened baseline. | [#12763](https://github.com/rwlove/home-ops/pull/12763) |
| kyverno was the only non-exempt namespace without the network-policy baseline component (admission controller with zero CNPs). | [#12764](https://github.com/rwlove/home-ops/pull/12764) |
| docs/labeler workflows queued superseded runs instead of cancelling (`cancel-in-progress`). | [#12765](https://github.com/rwlove/home-ops/pull/12765) |
| The dind runner sidecar was the repo's only untagged image (floating `:latest`); pinned tag+digest so Renovate tracks it. | [#12766](https://github.com/rwlove/home-ops/pull/12766) |

## Roadmap (tracked issues)

Ordered by priority. Each issue carries the full plan.

| # | Item | Theme |
|---|---|---|
| [#12767](https://github.com/rwlove/home-ops/issues/12767) | Migrate the 26 oauth2-proxy sidecars to Envoy Gateway `SecurityPolicy` extAuth via Authelia — retires ~4,900 lines, 26 Deployments, 26 OIDC clients. Piloted on one app first. | simplification + security |
| [#12768](https://github.com/rwlove/home-ops/issues/12768) | Default `SecurityPolicy` on the external gateway with explicit public-route exemptions (defense-in-depth; depends on #12767). | security |
| [#12769](https://github.com/rwlove/home-ops/issues/12769) | Audit `privileged: true` on the four AI GPU workloads — likely only need device-plugin requests. | security |
| [#12770](https://github.com/rwlove/home-ops/issues/12770) | Backfill the securityContext baseline on the 23 app-template HelmReleases that have none (lldap and the mutating webhook first). | security |
| [#12771](https://github.com/rwlove/home-ops/issues/12771) | Root-user audit: document or drop unjustified `runAsUser: 0`. | security |
| [#12772](https://github.com/rwlove/home-ops/issues/12772) | Backfill `resources:` on the 33 HelmReleases running BestEffort. | optimization |
| [#12773](https://github.com/rwlove/home-ops/issues/12773) | Right-size memory limits on non-GPU apps with 8G+ headroom. | optimization |
| [#12774](https://github.com/rwlove/home-ops/issues/12774) | Migrate five download-client config PVCs off Longhorn to ceph-block per storage rule 2. | optimization |
| [#12775](https://github.com/rwlove/home-ops/issues/12775) | Cluster-level Flux Kustomization defaults patch (~1,900 lines of ks.yaml boilerplate) + podSecurity/reloader values consolidation. | simplification |
| [#12776](https://github.com/rwlove/home-ops/issues/12776) | Housekeeping: dead `automation` namespace, one-liner tools, unused component repos, renovate config inlining. | simplification |
| [#12777](https://github.com/rwlove/home-ops/issues/12777) | Flux reconcile trims: 5m bootstrap intervals → 15m, `wait: true` audit, depth-7 dependsOn chains. | optimization |

## Explicitly deferred / not doing

- **Observability retention stays** (Prometheus 14d, Loki 30d) — the
  history is worth more than the ceph-block space here.
- **No mass securityContext retrofit** beyond the 23-gap list — the
  security instruction explicitly rejects churning working apps.
- **Upstream-chart HelmReleases without explicit securityContext**
  inherit well-maintained chart defaults; left alone.
- The 15 `# workaround:` annotations are healthy (each has an upstream
  link and removal condition) and stay with the upstream-watcher
  process.

## Notes for future audits

- Regenerate the gap lists rather than trusting the counts here:
  `grep -rL 'resources:' kubernetes/apps --include=helmrelease.yaml`
  and the securityContext equivalent.
- The flux-local CI moved to `ubuntu-latest` + flate and now renders in
  seconds — old "10–17 min on arc runners" figures predate that.
