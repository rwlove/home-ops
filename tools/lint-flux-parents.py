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
- the parents' paths are exactly the roots, one to one: ./kubernetes/apps plus
  every ./kubernetes/tiers/<t> that has a kustomization.yaml (a root no parent
  points at leaves its dirs unmanaged);
- the child-default patches are the expected three, including the
  home-ops.io/child-prune=disabled opt-out (removing it from every copy at once
  would still pass an identity check);
- `prune: false` only with the annotation home-ops.io/move-window naming the
  open window's PR (a forgotten one silently leaks removals);
- no other YAML in the dir: the sync Kustomization applies the whole directory,
  so a parent in a second file would skip this lint; and no kustomization.yaml
  with root-level patches (flate would discover 0 Kustomizations and exit 0).

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

    for f in sorted(base.iterdir()):
        if f.name != "ks.yaml" and f.suffix in (".yaml", ".yml"):
            errors.append(f"{f}: only ks.yaml belongs here (the sync Kustomization applies every file in {base})")
        if f.is_dir():
            errors.append(f"{f}: no subdirectories here (no kustomize Components: flate never applies them)")

    kfile = base / "kustomization.yaml"
    if kfile.exists():
        root = yaml.safe_load(kfile.read_text()) or {}
        for key in ("patches", "patchesStrategicMerge", "patchesJson6902"):
            if key in root:
                errors.append(f"{kfile}: root-level `{key}` makes flate test 0 Kustomizations")

    shared = {}
    paths = {}
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
        if spec.get("prune") is False and not (meta.get("annotations") or {}).get("home-ops.io/move-window"):
            errors.append(f"{where}: prune: false needs the annotation home-ops.io/move-window: \"<PR#>\" (a tier-move window); otherwise set prune: true")
        paths[name] = str(spec.get("path", "")).removeprefix("./").rstrip("/")
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
        patches = ref.get("patches") or []
        if len(patches) != 3:
            errors.append(f"{where}: expected the 3 child-default patches, found {len(patches)}")
        optout = [p for p in patches if (p.get("target") or {}).get("labelSelector") == "home-ops.io/child-prune=disabled"]
        if len(optout) != 1 or "prune: false" not in str(optout[0].get("patch", "")):
            errors.append(f"{where}: missing the home-ops.io/child-prune=disabled -> prune: false opt-out patch")
        repo = base.parents[2] if len(base.parents) > 2 else pathlib.Path(".")
        roots = {"kubernetes/apps"} | {str(k.parent.relative_to(repo)) for k in (repo / "kubernetes/tiers").glob("*/kustomization.yaml")}
        got = sorted(paths.values())
        if len(set(got)) != len(got):
            errors.append(f"{base}/ks.yaml: two parents share a path: {got}")
        if set(got) != roots:
            errors.append(f"{base}/ks.yaml: parent paths {sorted(set(got))} must equal the roots {sorted(roots)} one to one")
        if (ref.get("sourceRef") or {}).get("name") != "home-ops-kubernetes":
            errors.append(f"{where}: sourceRef must be home-ops-kubernetes")
        if not any(d.get("name") == "cluster-config" for d in ref.get("dependsOn") or []):
            errors.append(f"{where}: dependsOn must include cluster-config")

    for e in errors:
        print(e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
