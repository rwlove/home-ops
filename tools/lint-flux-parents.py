#!/usr/bin/env python3
"""Lint the Flux tier parents in kubernetes/flux/cluster/ks.yaml.

Every parent Kustomization (everything in ks.yaml except cluster-config)
carries its full spec inline. A kustomize Component would remove the
duplication, but flate (CI) never applies Component patches and its diff
can't resolve the parents' dependencies through one, so CI would stop
modelling the child defaults. The parents are copies of one another instead,
and this lint keeps the copies identical.

Checks:
- each parent: label home-ops.io/tier-parent: "true", namespace flux-system;
- every parent's spec, apart from `path` and `prune`, is identical (prune
  varies only while a move window is open);
- that shared spec carries what every parent needs: deletionPolicy Orphan
  (removing a parent must not delete its children), both substitution sources
  (manifests with $${...} escapes only render through them), sourceRef,
  dependsOn cluster-config, and the child-default spec.patches;
- no kustomization.yaml with root-level patches in the dir (flate would
  discover 0 Kustomizations and exit 0).

Usage: lint-flux-parents.py [DIR]   (default: kubernetes/flux/cluster)
"""

import pathlib
import sys

import yaml

LABEL = "home-ops.io/tier-parent"
NON_PARENTS = {"cluster-config"}
PER_PARENT = {"path", "prune"}


def main() -> int:
    base = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "kubernetes/flux/cluster")
    errors = []

    kfile = base / "kustomization.yaml"
    if kfile.exists():
        root = yaml.safe_load(kfile.read_text()) or {}
        for key in ("patches", "patchesStrategicMerge", "patchesJson6902"):
            if key in root:
                errors.append(f"{kfile}: root-level `{key}` makes flate test 0 Kustomizations")

    shared = {}
    for doc in yaml.safe_load_all((base / "ks.yaml").read_text()):
        if not isinstance(doc, dict) or doc.get("kind") != "Kustomization":
            continue
        meta = doc.get("metadata") or {}
        name = meta.get("name", "?")
        if name in NON_PARENTS:
            continue
        where = f"{base}/ks.yaml: Kustomization/{name}"
        if (meta.get("labels") or {}).get(LABEL) != "true":
            errors.append(f'{where}: needs label {LABEL}: "true"')
        if meta.get("namespace") != "flux-system":
            errors.append(f"{where}: needs namespace: flux-system")
        spec = doc.get("spec") or {}
        if not spec.get("path"):
            errors.append(f"{where}: set path")
        if not isinstance(spec.get("prune"), bool):
            errors.append(f"{where}: set prune explicitly")
        shared[name] = {k: v for k, v in spec.items() if k not in PER_PARENT}

    if not shared:
        errors.append(f"{base}/ks.yaml: no tier parents found")
    else:
        first = next(iter(shared))
        ref = shared[first]
        for name, s in shared.items():
            if s != ref:
                keys = sorted(k for k in set(s) | set(ref) if s.get(k) != ref.get(k))
                errors.append(f"{base}/ks.yaml: Kustomization/{name}: spec differs from {first} in {', '.join(keys)} (only path and prune may differ)")
        where = f"{base}/ks.yaml: Kustomization/{first}"
        if ref.get("deletionPolicy") != "Orphan":
            errors.append(f"{where}: deletionPolicy must be Orphan")
        if len(((ref.get("postBuild") or {}).get("substituteFrom")) or []) < 2:
            errors.append(f"{where}: postBuild.substituteFrom must list cluster-config and cluster-secrets")
        if not ref.get("patches"):
            errors.append(f"{where}: spec.patches (the child defaults) is missing")
        if (ref.get("sourceRef") or {}).get("name") != "home-ops-kubernetes":
            errors.append(f"{where}: sourceRef must be home-ops-kubernetes")
        if not any(d.get("name") == "cluster-config" for d in ref.get("dependsOn") or []):
            errors.append(f"{where}: dependsOn must include cluster-config")

    for e in errors:
        print(e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
