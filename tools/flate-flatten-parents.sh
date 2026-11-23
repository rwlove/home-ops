#!/usr/bin/env bash
# Render the Flux parents in <repo>/kubernetes/flux/cluster into one plain
# ks.yaml, in place, for flate (CI-only; never run this in a working tree you
# care about).
#
# flate reads the parent Kustomizations' own YAML and applies their inline
# spec.patches to children, but it does not build the directory, so patches a
# kustomize Component adds (components/tier-defaults) are invisible to it.
# Without this step flate renders every child without the child-default
# patches. Rendering with kustomize first gives flate exactly the objects
# kustomize-controller applies.
#
# Usage: flate-flatten-parents.sh <repo-root>
set -euo pipefail
dir="$1/kubernetes/flux/cluster"
[ -f "$dir/kustomization.yaml" ] || { echo "no $dir/kustomization.yaml; nothing to flatten"; exit 0; }
out="$(mktemp)"
kubectl kustomize "$dir" > "$out"
grep -q '^kind: Kustomization$' "$out" || { echo "::error::rendered $dir has no Kustomizations"; exit 1; }
find "$dir" -mindepth 1 -delete
mv "$out" "$dir/ks.yaml"
echo "flattened $dir: $(grep -c '^kind: Kustomization$' "$dir/ks.yaml") parent Kustomizations"
