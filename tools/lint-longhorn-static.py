#!/usr/bin/env python3
"""Reject PersistentVolumeClaims that bind a Longhorn StorageClass dynamically.

Longhorn in this repo is the *irreplaceable-data* tier, and the standing
convention (`.agents/instructions/storage-class.instructions.md`) is that a
Longhorn volume is ALWAYS statically provisioned: a readable-named Longhorn
`Volume` CR under `longhorn/volumes/`, a static PV pinning it via
`csi.volumeHandle`, and a PVC that binds it with an explicit `volumeName`.
Dynamic Longhorn provisioning (a PVC on a Longhorn-provisioner StorageClass
with no `volumeName`) produces an opaque `pvc-<uuid>` volume that is neither
greppable nor restorable by name — the exact anti-pattern homebox + grocy hit
(#15538), which then needed a full rename-via-copy migration (#16045) to undo.

ceph-block (rook-ceph) is the dynamic *durable-in-cluster* default and is NOT
affected by this lint — only Longhorn-provisioner StorageClasses are.

This lint catches the regression at PR time by scanning every PVC in the repo
and failing on any that bind a Longhorn StorageClass without a `volumeName`.
Any such change must instead be authored as a static volume — route it to the
storage-operator.

Usage:
    tools/lint-longhorn-static.py [PATH ...]

If no PATHs are given, scans `kubernetes/`. Supports multi-document YAML
files. Exits 0 if all Longhorn PVCs are static, 1 if any bind dynamically.
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

LONGHORN_PROVISIONER = "driver.longhorn.io"
# Chart-provided Longhorn StorageClasses that are not defined as YAML manifests
# in this repo (they ship with the Longhorn HelmRelease), so they can't be
# discovered by scanning. A PVC referencing one of these still must be static.
BUILTIN_LONGHORN_SCS = {"longhorn", "longhorn-static"}


def _iter_yaml_docs(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return [("__read_error__", str(e))]
    try:
        return list(yaml.safe_load_all(text))
    except yaml.YAMLError as e:
        return [("__parse_error__", str(e))]


def _is_doc(doc: object, kind: str) -> bool:
    return isinstance(doc, dict) and doc.get("kind") == kind


def collect_longhorn_scs(files: list[Path]) -> set[str]:
    """Names of all StorageClasses in the repo whose provisioner is Longhorn,
    plus the chart-provided builtins."""
    names: set[str] = set(BUILTIN_LONGHORN_SCS)
    for path in files:
        for doc in _iter_yaml_docs(path):
            if not _is_doc(doc, "StorageClass"):
                continue
            if doc.get("provisioner") != LONGHORN_PROVISIONER:
                continue
            meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
            name = meta.get("name")
            if isinstance(name, str) and name:
                names.add(name)
    return names


def lint_file(path: Path, longhorn_scs: set[str]) -> list[str]:
    """Return a list of error messages for this file (empty if clean)."""
    errors: list[str] = []
    docs = _iter_yaml_docs(path)
    for idx, doc in enumerate(docs):
        if isinstance(doc, tuple) and doc[0] in ("__read_error__", "__parse_error__"):
            # yamllint owns read/parse errors; skip silently here.
            return []
        if not _is_doc(doc, "PersistentVolumeClaim"):
            continue
        spec = doc.get("spec") if isinstance(doc.get("spec"), dict) else None
        if not isinstance(spec, dict):
            continue
        sc = spec.get("storageClassName")
        if not isinstance(sc, str) or sc not in longhorn_scs:
            continue
        # A Longhorn-backed PVC MUST pin a volume by name (static).
        if spec.get("volumeName"):
            continue
        meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        name = meta.get("name", "<unnamed>")
        ns = meta.get("namespace", "<no-ns>")
        doc_loc = f" (doc {idx})" if len(docs) > 1 else ""
        errors.append(
            f"{path}{doc_loc}: PVC {ns}/{name} binds Longhorn StorageClass "
            f"'{sc}' with no volumeName — this is DYNAMIC Longhorn provisioning "
            f"(opaque pvc-<uuid> volume), which is prohibited. Longhorn is "
            f"static-named only: add a readable-named Volume CR under "
            f"longhorn/volumes/, a static PV with csi.volumeHandle, and set "
            f"volumeName on this PVC. Route the change to the storage-operator. "
            f"(If the data is regenerable/in-cluster-durable, use ceph-block "
            f"instead.) See .agents/instructions/storage-class.instructions.md."
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

    # StorageClasses are repo-global, so discover them across the whole repo
    # (not just the staged subset) to avoid false negatives when only a PVC
    # file is in scope — UNION the scanned paths so a self-contained new app
    # (its new Longhorn SC + dynamic PVC in the same changeset) is caught even
    # when the files live outside kubernetes/ or are passed explicitly.
    sc_scan_roots = sorted(set(find_yaml_files([Path("kubernetes")])) | set(files))
    longhorn_scs = collect_longhorn_scs(sc_scan_roots)

    all_errors: list[str] = []
    for f in files:
        all_errors.extend(lint_file(f, longhorn_scs))

    if all_errors:
        print(
            "ERROR: dynamic Longhorn PVC detected — Longhorn must be "
            "static-named (route storage config to the storage-operator):",
            file=sys.stderr,
        )
        for msg in all_errors:
            print(f"  {msg}", file=sys.stderr)
        print(
            f"\n{len(all_errors)} dynamic Longhorn PVC(s) found.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
