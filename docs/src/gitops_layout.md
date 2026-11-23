# GitOps layout: Flux tier parents

Flux delivers the cluster through four **tier parent** Kustomizations. A render
failure in one tier stalls only that tier: before the split, one bad child or
one admission-webhook 429 stopped GitOps delivery cluster-wide.

## The parents

| Parent | Root | Namespace dirs (`kubernetes/apps/…`) |
|---|---|---|
| `cluster-infra` | `kubernetes/tiers/infra` | kube-system, network, cert-manager, cilium-secrets, external-secrets, rook-ceph, longhorn-system, storage, databases, istio-system, flux-system, auth, observability |
| `cluster-platform` | `kubernetes/tiers/platform` | mcp-system, kuadrant (disabled), ai, ate-system, actions-runner-system, actions-runner-privileged, renovate, tools |
| `cluster-apps` | `kubernetes/apps` | home, selfhosted, collab |
| `cluster-media` | `kubernetes/tiers/media` | media, downloads |

`cluster-config` (`kubernetes/flux/meta`) is not a tier parent. It provides the
substitution sources every parent reads, and each parent depends on it.

```text
kubernetes/flux/cluster/ks.yaml              # cluster-config + the 4 parents (built by the sync
                                             # Kustomization home-ops-kubernetes)
kubernetes/tiers/<tier>/kustomization.yaml   # resources: ../../apps/<dir>, …
kubernetes/apps/kustomization.yaml           # the cluster-apps root
```

`kubernetes/tiers/` is a sibling of `kubernetes/apps/` because every directory
under `kubernetes/apps/` is a namespace.

## Rules

- **The parents are identical copies.** Every tier parent carries the full
  spec inline: interval, `retryInterval: 2m`, `timeout: 10m`, `wait: false`,
  `sourceRef`, `dependsOn: cluster-config`, `postBuild.substituteFrom`,
  `deletionPolicy: Orphan`, and `spec.patches` (the child defaults). Only
  `path` and `prune` differ. `tools/lint-flux-parents.py` (pre-commit + CI)
  keeps the copies identical, so to change a shared setting, change it on all
  four in the same PR.
  - Why copies and not a kustomize Component: flate (CI) never applies
    Component patches, and its diff can't resolve the parents' dependencies
    through one, so CI would stop modelling the child defaults.
  - `postBuild` is not optional: manifests that use `$${…}` escapes (the
    node-tuning DaemonSet's script, for one) only render correctly through
    parent substitution.
- **Exactly once.** Every `kubernetes/apps/<dir>` is listed by exactly one root,
  or commented out in exactly one (disabled). `tools/lint-tier-membership.py`
  enforces it. An unlisted dir is silently unmanaged; a dir listed twice makes
  two parents fight over its owner labels.
- **No `kustomization.yaml` with root-level patches** in
  `kubernetes/flux/cluster/`. With them, flate discovers 0 Kustomizations and
  exits 0. The flate workflow guards this and fails below 300 tested
  Kustomizations.
- **`deletionPolicy: Orphan` on every parent, permanently.** If a parent object
  is removed from `ks.yaml`, the sync Kustomization deletes it. With Orphan its
  children survive; with WaitForTermination they would all be deleted.
- **Never delete or rename a parent by hand.** A rename is a delete plus a
  create, so it's a prune.

## Adding a namespace

1. Create `kubernetes/apps/<ns>/` (namespace.yaml, kustomization.yaml, apps).
2. Add it to exactly one tier root. Platform providers (CRDs, operators,
   storage, networking, secrets, auth, observability) go to infra. Agent, AI and
   CI tooling goes to platform. Household apps go to apps. Media goes to media.
3. `kubectl kustomize` that root, and run `tools/lint-tier-membership.py`.

Disabling an app or a namespace (commenting it out) is a **removal**. Under a
parent with `prune: true` it garbage-collects the objects. Under `prune: false`
it only drops them from the inventory; nothing is deleted, and nothing is
managed any more either.

## Protected objects

A literal `kustomize.toolkit.fluxcd.io/prune: disabled` label (in the source
manifest, never through `commonMetadata`, which is applied after the build)
stops garbage collection of an object under any parent setting. It is set on
every parent-rendered object whose deletion cascades cluster-wide or destroys
data:

- the Flux Kustomizations (`flux-instance`, `flux-operator`);
- the API VIP (the kube-vip DaemonSet, ServiceAccount, ClusterRole and
  ClusterRoleBinding);
- `cilium`, `coredns`, `external-secrets`, `onepassword-connect`,
  `rook-ceph-cluster`, `longhorn`, `longhorn-volumes`;
- the Kustomizations that own the CNPG Postgres clusters;
- the PriorityClasses;
- every Namespace.

Retiring a protected object takes a manual delete after its Git removal.

## Moving a dir between parents

A move changes which parent owns a set of objects. Garbage collection deletes an
object only if its live owner labels still name the pruning parent, but two
pruning parents on independent timers can race. So a move always runs in a
**window** in which every involved parent is `prune: false`:

1. Freeze writers: suspend the home-ops RenovateJob, and merge nothing else.
   Anything removed from a parent's root during the window is dropped from its
   inventory rather than collected.
2. PR: the source (and, if it already exists, the destination) parent →
   `prune: false`, each with the annotation
   `home-ops.io/move-window: "<this PR's number>"`. `tools/lint-flux-parents.py`
   rejects `prune: false` without it, so a window can't be left open by
   accident.
3. PR (single commit): add or extend the destination root, and remove the dirs
   from the source root. A new parent is created with `prune: false` and the
   same `home-ops.io/move-window` annotation. Before
   pushing: build every root, check the union of all roots has no duplicates
   and equals the pre-window set, and run both lints.
4. Reconcile destination → source → destination. Check:
   - each parent's inventory equals its render, and the moved objects have left
     the source;
   - owner labels name the new parent;
   - no child Kustomization's `generation` changed;
   - nothing was deleted.
5. Leak check: no object labelled for a parent is missing from its inventory.
6. PR: every involved parent → `prune: true` (and drop the
   `home-ops.io/move-window` annotation), once each parent's
   `lastAppliedRevision` includes the move. Then unfreeze.

A correction inside the window (a dir in the wrong tier) is safe while every
side is `prune: false`. After the window closes, any further move needs a new
window.
