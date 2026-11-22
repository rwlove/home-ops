#!/usr/bin/env python3
"""Lint child Flux Kustomizations under kubernetes/apps.

Every child Kustomization (kustomize.toolkit.fluxcd.io/v1, kind Kustomization)
must:

- set `wait` explicitly. It defaults to false; making it explicit keeps each
  Kustomization's dependency semantics readable. See
  .agents/instructions/flux.dependencies.md for when `wait: true` is warranted.
- set `timeout` explicitly. Unset, it defaults to the interval (30m/1h), so a
  stuck health check holds for up to an hour.
- NOT set `sourceRef`, `retryInterval`, or `prune: true`. The cluster-apps
  parent patch (kubernetes/flux/cluster/ks.yaml) injects all three and
  overrides whatever the child declares, so a child value is dead config that
  only looks tunable.
  `prune: false` stays allowed as documentation for the label-selected opt-out
  (home-ops.io/child-prune: disabled).

Usage: lint-flux-ks.py [ROOT ...]   (default: kubernetes/apps)
"""

import pathlib
import sys

import yaml


class _Loader(yaml.SafeLoader):
    pass


# Tolerate custom tags (e.g. ESPHome !secret) in unrelated files.
_Loader.add_multi_constructor("!", lambda loader, suffix, node: None)


def check(path: pathlib.Path) -> list[str]:
    errors = []
    try:
        docs = list(yaml.load_all(path.read_text(), Loader=_Loader))
    except yaml.YAMLError as exc:
        return [f"{path}: unparseable YAML: {exc}"]
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        if doc.get("kind") != "Kustomization" or not str(doc.get("apiVersion", "")).startswith(
            "kustomize.toolkit.fluxcd.io/"
        ):
            continue
        name = (doc.get("metadata") or {}).get("name", "?")
        spec = doc.get("spec") or {}
        where = f"{path}: Kustomization/{name}"
        if "wait" not in spec:
            errors.append(f"{where}: set `wait` explicitly (default false)")
        if "timeout" not in spec:
            errors.append(f"{where}: set `timeout` explicitly (default = interval)")
        if "sourceRef" in spec:
            errors.append(f"{where}: drop `sourceRef` (injected by the cluster-apps parent patch)")
        if "retryInterval" in spec:
            errors.append(f"{where}: drop `retryInterval` (injected by the cluster-apps parent patch: 2m)")
        if spec.get("prune") is True:
            errors.append(f"{where}: drop `prune: true` (injected by the cluster-apps parent patch)")
    return errors


def main() -> int:
    roots = [pathlib.Path(p) for p in (sys.argv[1:] or ["kubernetes/apps"])]
    errors = []
    for root in roots:
        files = [root] if root.is_file() else sorted(root.rglob("ks.yaml"))
        for f in files:
            if f.name == "ks.yaml":
                errors.extend(check(f))
    for e in errors:
        print(e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
