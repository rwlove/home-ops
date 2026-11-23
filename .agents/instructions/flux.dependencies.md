# Flux Kustomization dependencies, wait and timeout

How child Flux Kustomizations (`kubernetes/apps/**/ks.yaml`) declare
`dependsOn`, `wait` and `timeout`. `tools/lint-flux-ks.py` (pre-commit + CI)
enforces the mechanical parts.

## What `dependsOn` actually waits for

A `dependsOn` edge waits until the target Kustomization is **Ready**. What
"Ready" means depends on the target:

- target `wait: true` (or `healthChecks` / `healthCheckExprs`): Ready means
  its resources are **healthy**, so the edge is **hard**.
- target `wait: false` with no health checks: Ready means its manifests were
  **applied**, so the edge is **soft** (ordering only).

## Rule: hard dependencies only for platform providers

Use `wait: true` (plus `healthCheckExprs` where the default kstatus check is
wrong) **only** on Kustomizations that provide something their dependents
cannot even be applied or admitted without:

- CRDs
- admission webhooks
- operators that reconcile the dependents' custom resources
- storage classes / CSI drivers

Everything else, including apps that other apps call at runtime (databases,
brokers, auth, inference servers), stays `wait: false`. A dependent that
starts before its runtime dependency crash-loops briefly and self-heals.
Making the edge hard instead turns that dependency into a **delivery freeze**
for every dependent whenever it's unhealthy. For example, ~27 apps depend on
Authelia; a hard edge would block all of them from receiving any update
during an Authelia incident.

## Always explicit

- `wait`: always set (`false` unless the rule above applies).
- `timeout`: always set. Unset, it defaults to the **interval** (30m/1h), so a
  stuck health check holds that long. Repo norm: `5m` for `wait: false`;
  `10m`–`15m` for `wait: true` operators with slow cold starts.
- Hub operators (longhorn, multus, node-feature-discovery, rook-ceph) run
  `wait: true` + `timeout: 3m`. That's backed by 0 health-check timeouts in 29
  days (2026-11-22). Raise only on evidence.

## Never set on a child

- `sourceRef`, `retryInterval` and `prune: true`: the tier parents' patch
  (`spec.patches` on each parent in `kubernetes/flux/cluster/ks.yaml`) injects all three and **overrides** any child
  value (strategic merge is patch-wins). It sets `retryInterval: 2m`.
- To opt a child out of pruning, label it `home-ops.io/child-prune: disabled`
  (literal in its `ks.yaml`; `prune: false` may stay as documentation).
- To protect a parent-rendered object from garbage collection entirely, label
  it `kustomize.toolkit.fluxcd.io/prune: disabled`. Reserve that for objects
  whose deletion cascades cluster-wide or destroys data.

## Redundant edges are fine

An edge implied transitively (A → B → C, plus A → C) is harmless and documents
A's direct need. Keep it.
