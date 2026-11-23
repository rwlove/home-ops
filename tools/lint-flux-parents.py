#!/usr/bin/env python3
"""Lint the Flux tier parents in kubernetes/flux/cluster.

Every parent Kustomization (everything in ks.yaml except cluster-config) gets
its shared settings from the tier-defaults Component, which selects parents by
the label home-ops.io/tier-parent=true. A labelSelector that matches nothing is
not a build error, so a parent missing the label would ship without sourceRef,
substitution, Orphan or the child-default patches, and nothing would fail until
the live apply. This lint makes that a CI failure instead.

Checks:
- each parent: label home-ops.io/tier-parent: "true", namespace flux-system,
  spec has only `path`, `prune` and `patches`;
- every parent's spec.patches (the child defaults) is identical. They stay
  inline because flate applies a parent's own spec.patches but never a
  Component's, so moving them into the Component would blind CI;
- the root kustomization.yaml lists the Component and has no root-level
  patches (with those, flate discovers 0 Kustomizations and exits 0);
- the Component targets the label and carries the settings every parent needs.

Usage: lint-flux-parents.py [DIR]   (default: kubernetes/flux/cluster)
"""

import pathlib
import sys

import yaml

LABEL = "home-ops.io/tier-parent"
NON_PARENTS = {"cluster-config"}


def main() -> int:
    base = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "kubernetes/flux/cluster")
    errors = []

    root = yaml.safe_load((base / "kustomization.yaml").read_text())
    for key in ("patches", "patchesStrategicMerge", "patchesJson6902"):
        if key in root:
            errors.append(f"{base}/kustomization.yaml: root-level `{key}` makes flate test 0 Kustomizations; use the Component")
    if "./components/tier-defaults" not in (root.get("components") or []):
        errors.append(f"{base}/kustomization.yaml: must list ./components/tier-defaults")

    parents = 0
    patch_sets = {}
    for doc in yaml.safe_load_all((base / "ks.yaml").read_text()):
        if not isinstance(doc, dict) or doc.get("kind") != "Kustomization":
            continue
        meta = doc.get("metadata") or {}
        name = meta.get("name", "?")
        if name in NON_PARENTS:
            continue
        parents += 1
        where = f"{base}/ks.yaml: Kustomization/{name}"
        if (meta.get("labels") or {}).get(LABEL) != "true":
            errors.append(f'{where}: needs label {LABEL}: "true" (else tier-defaults silently skips it)')
        if meta.get("namespace") != "flux-system":
            errors.append(f"{where}: needs namespace: flux-system")
        spec = doc.get("spec") or {}
        extra = sorted(set(spec) - {"path", "prune", "patches"})
        if extra:
            errors.append(f"{where}: declare only path, prune and patches; {', '.join(extra)} belong in components/tier-defaults")
        if not spec.get("patches"):
            errors.append(f"{where}: spec.patches (the child defaults) is missing")
        patch_sets[name] = yaml.safe_dump(spec.get("patches"), sort_keys=True)
        if not isinstance(spec.get("prune"), bool):
            errors.append(f"{where}: set prune explicitly")
        if not spec.get("path"):
            errors.append(f"{where}: set path")
    if parents == 0:
        errors.append(f"{base}/ks.yaml: no tier parents found")
    if len(set(patch_sets.values())) > 1:
        first = next(iter(patch_sets))
        differ = [n for n, v in patch_sets.items() if v != patch_sets[first]]
        errors.append(f"{base}/ks.yaml: spec.patches must be identical on every parent; {', '.join(differ)} differ from {first}")

    comp_dir = base / "components/tier-defaults"
    comp = yaml.safe_load((comp_dir / "kustomization.yaml").read_text())
    targets = [p for p in comp.get("patches") or [] if (p.get("target") or {}).get("labelSelector") == f"{LABEL}=true"]
    if len(targets) != 1:
        errors.append(f"{comp_dir}/kustomization.yaml: expected exactly one patch targeting labelSelector {LABEL}=true")
    else:
        patch = yaml.safe_load((comp_dir / targets[0]["path"]).read_text())
        spec = patch.get("spec") or {}
        where = f"{comp_dir}/{targets[0]['path']}"
        if spec.get("deletionPolicy") != "Orphan":
            errors.append(f"{where}: deletionPolicy must be Orphan")
        if len(((spec.get("postBuild") or {}).get("substituteFrom")) or []) < 2:
            errors.append(f"{where}: postBuild.substituteFrom must list cluster-config and cluster-secrets")
        if "patches" in spec:
            errors.append(f"{where}: spec.patches must stay inline on each parent (flate never applies Component patches)")
        if (spec.get("sourceRef") or {}).get("name") != "home-ops-kubernetes":
            errors.append(f"{where}: sourceRef must be home-ops-kubernetes")
        if not any(d.get("name") == "cluster-config" for d in spec.get("dependsOn") or []):
            errors.append(f"{where}: dependsOn must include cluster-config")
        if "prune" in spec:
            errors.append(f"{where}: prune is per parent; don't set it here (the patch would win)")

    for e in errors:
        print(e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
