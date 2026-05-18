#!/usr/bin/env python3
"""Reject CiliumNetworkPolicy manifests that select endpoints but define no rules.

Cilium 1.19 rejects CNPs with an `endpointSelector.matchLabels` but no
`ingress:` or `egress:` rules (or both empty) with
`spec.ingress: Required value`. The CNP commits cleanly and kustomize
builds it, but Cilium refuses to apply it — silently, unless someone is
watching Flux logs. Under enforced default-deny the selected pods get
zero per-app allows.

This lint catches the bug at PR time by scanning every CNP file in the
repo and failing on any that match the empty-rules shape.

Usage:
    tools/lint-cnp-empty-rules.py [PATH ...]

If no PATHs are given, scans `kubernetes/`. Supports multi-document YAML
files. Exits 0 if all CNPs are valid, 1 if any are not.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print(
        "ERROR: PyYAML is required. Install with: pip install pyyaml",
        file=sys.stderr,
    )
    sys.exit(2)


def _is_cnp(doc: object) -> bool:
    return (
        isinstance(doc, dict)
        and doc.get("kind") in {"CiliumNetworkPolicy", "CiliumClusterwideNetworkPolicy"}
        and isinstance(doc.get("apiVersion"), str)
        and doc["apiVersion"].startswith("cilium.io/")
    )


def _has_endpoint_selector(spec: dict) -> bool:
    """A CNP "selects endpoints" when endpointSelector.matchLabels is
    non-empty OR endpointSelector.matchExpressions is non-empty OR
    nodeSelector is set. An empty `endpointSelector: {}` matches all
    endpoints in the namespace, which is also a real selection."""
    if not isinstance(spec, dict):
        return False
    # nodeSelector (clusterwide) — still needs rules.
    if spec.get("nodeSelector"):
        return True
    es = spec.get("endpointSelector")
    if es is None:
        return False
    if not isinstance(es, dict):
        return True  # weird shape, treat as selecting
    # Empty `endpointSelector: {}` matches everything — also a selection.
    if es == {}:
        return True
    if es.get("matchLabels"):
        return True
    if es.get("matchExpressions"):
        return True
    return False


def _has_rules(spec: dict) -> bool:
    """A CNP has rules when ingress, egress, ingressDeny, or egressDeny
    is a non-empty list."""
    if not isinstance(spec, dict):
        return False
    for key in ("ingress", "egress", "ingressDeny", "egressDeny"):
        v = spec.get(key)
        if isinstance(v, list) and len(v) > 0:
            return True
    return False


def _iter_yaml_docs(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return [("__read_error__", str(e))]
    try:
        return list(yaml.safe_load_all(text))
    except yaml.YAMLError as e:
        return [("__parse_error__", str(e))]


def lint_file(path: Path) -> list[str]:
    """Return a list of error messages for this file (empty if clean)."""
    errors: list[str] = []
    docs = _iter_yaml_docs(path)
    for idx, doc in enumerate(docs):
        if isinstance(doc, tuple) and doc[0] in ("__read_error__", "__parse_error__"):
            # Don't fail the CNP lint on unrelated read/parse problems;
            # yamllint covers those. Skip silently.
            return []
        if not _is_cnp(doc):
            continue
        spec = doc.get("spec") if isinstance(doc, dict) else None
        if not isinstance(spec, dict):
            continue
        if not _has_endpoint_selector(spec):
            continue
        if _has_rules(spec):
            continue
        name = (
            doc.get("metadata", {}).get("name", "<unnamed>")
            if isinstance(doc.get("metadata"), dict)
            else "<unnamed>"
        )
        ns = (
            doc.get("metadata", {}).get("namespace", "<no-ns>")
            if isinstance(doc.get("metadata"), dict)
            else "<no-ns>"
        )
        kind = doc.get("kind", "CiliumNetworkPolicy")
        doc_loc = f" (doc {idx})" if len(docs) > 1 else ""
        errors.append(
            f"{path}{doc_loc}: {kind} {ns}/{name} selects endpoints but has no "
            f"ingress/egress rules — Cilium 1.19 will reject with "
            f"'spec.ingress: Required value'. Either delete the CNP "
            f"(baseline allows still apply) or add the minimal explicit "
            f"ingress rule for the dominant flow."
        )
    return errors


def find_yaml_files(roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        if root.is_file():
            if root.suffix in (".yaml", ".yml"):
                found.append(root)
            continue
        if not root.is_dir():
            continue
        for ext in ("*.yaml", "*.yml"):
            found.extend(root.rglob(ext))
    return sorted(set(found))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan (default: kubernetes/)",
    )
    args = parser.parse_args(argv)

    roots = args.paths or [Path("kubernetes")]
    files = find_yaml_files(roots)

    all_errors: list[str] = []
    for f in files:
        all_errors.extend(lint_file(f))

    if all_errors:
        print(
            "ERROR: empty-rules CiliumNetworkPolicy detected — these CNPs will "
            "be rejected by Cilium 1.19 and silently fail to apply:",
            file=sys.stderr,
        )
        for msg in all_errors:
            print(f"  {msg}", file=sys.stderr)
        print(
            f"\n{len(all_errors)} empty-rules CNP(s) found. See "
            f"`.agents/instructions/` or memory `project_cilium_cnp_empty_rules_invalid`.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
