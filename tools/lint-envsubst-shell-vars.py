#!/usr/bin/env python3
"""Reject unescaped ${VAR} inside container command/args.

Flux postBuild envsubst runs in STRICT mode over every built manifest, and
it substitutes the BRACED form `${VAR}`. A shell script embedded in a
container's `command`/`args` therefore cannot write `${MY_VAR}` for a shell
variable: Flux reads it as a substitution variable and, when it is not
defined in cluster-config/cluster-secrets, fails the whole Kustomization:

    post build failed for 'CronJob.v1.batch/windmill-watchdog':
    envsubst error: variable substitution failed:
    variable not set (strict mode): "WINDMILL_TOKEN"

Nothing catches this before merge — `flate` cannot read cluster-secrets
offline, so it renders the vars empty and passes. The break only appears at
reconcile time, and it blocks every subsequent change to that Kustomization
until someone notices. This happened on 2026-07-26 (home-ops#13318, fixed
in #13321).

The fix is to escape shell references as `$${VAR}`, which envsubst emits as
a literal `${VAR}`. See ai/opencode/app/helmrelease.yaml for the convention.

Scope, deliberately narrow so this stays zero-false-positive:
  * Only `command` and `args` of `containers` / `initContainers`.
  * Only the BRACED `${VAR}` form. Unbraced `$VAR` is left alone — this
    repo has ~16 live instances ($HOME, $AFTER, ...) that reconcile fine,
    which is empirical proof envsubst does not touch the unbraced form.
  * `$${VAR}` (already escaped) is fine and is what this lint asks for.

If you genuinely want a Flux substitution variable inside a container
command, this lint will flag it. That is intentional: it is worth an
explicit look, because it means the container's behaviour depends on
cluster-config/cluster-secrets rendering correctly.

Usage:
    tools/lint-envsubst-shell-vars.py [PATH ...]

If no PATHs are given, scans `kubernetes/`. Supports multi-document YAML.
Exits 0 if clean, 1 if any unescaped reference is found.
"""
from __future__ import annotations

import argparse
import re
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

# A `$` that is NOT preceded by another `$`, followed by {IDENTIFIER}.
# The negative lookbehind is what distinguishes ${VAR} (eaten by envsubst)
# from $${VAR} (survives as a literal ${VAR} for the shell).
UNESCAPED_BRACED = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

CONTAINER_KEYS = ("containers", "initContainers")
SCRIPT_FIELDS = ("command", "args")


def _iter_containers(node: object):
    """Yield every container-like dict anywhere in the document.

    Walks the whole tree rather than assuming a fixed path, so this works
    for bare Pods, Deployments, CronJob jobTemplates, and HelmRelease
    `values:` blocks that inline a pod spec.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key in CONTAINER_KEYS and isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        yield item
            yield from _iter_containers(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_containers(item)


def _container_name(container: dict) -> str:
    name = container.get("name")
    return name if isinstance(name, str) else "<unnamed>"


def _iter_yaml_docs(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        return list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        # Unrelated parse problems are yamllint's job, not ours.
        return []


def lint_file(path: Path) -> list[str]:
    errors: list[str] = []
    for doc in _iter_yaml_docs(path):
        if not doc:
            continue
        for container in _iter_containers(doc):
            for field in SCRIPT_FIELDS:
                value = container.get(field)
                if not value:
                    continue
                entries = value if isinstance(value, list) else [value]
                for entry in entries:
                    if not isinstance(entry, str):
                        continue
                    seen: list[str] = []
                    for match in UNESCAPED_BRACED.finditer(entry):
                        if match.group(1) not in seen:
                            seen.append(match.group(1))
                    if seen:
                        rendered = ", ".join(f"${{{v}}}" for v in seen)
                        fixed = ", ".join(f"$${{{v}}}" for v in seen)
                        errors.append(
                            f"{path}: container '{_container_name(container)}' "
                            f"{field} contains unescaped {rendered} — Flux "
                            f"postBuild envsubst will consume these and fail "
                            f"the Kustomization in strict mode. Write them as "
                            f"{fixed} instead."
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

    files = find_yaml_files(args.paths or [Path("kubernetes")])

    all_errors: list[str] = []
    for f in files:
        all_errors.extend(lint_file(f))

    if all_errors:
        print(
            "ERROR: unescaped ${VAR} found inside container command/args — "
            "Flux postBuild envsubst runs in strict mode and will break "
            "reconciliation of the whole Kustomization:",
            file=sys.stderr,
        )
        for msg in all_errors:
            print(f"  {msg}", file=sys.stderr)
        print(
            f"\n{len(all_errors)} unescaped reference(s) found. Escape shell "
            f"variables as $${{VAR}}; see ai/opencode/app/helmrelease.yaml "
            f"and kubernetes/apps/home/windmill/README.md.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
