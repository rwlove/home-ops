#!/usr/bin/env python3
"""Reject unescaped ESPHome ${var} substitutions in git-tracked device configs.

The ESPHome device configs under
`kubernetes/apps/home/esphome/app/resources/` are baked into the
`esphome-devices` ConfigMap via `configMapGenerator`. Flux postBuild
envsubst then runs in STRICT mode over that rendered ConfigMap, and it
substitutes the BRACED form `${var}`. ESPHome's own substitutions
(`${name}`, `${friendly_name}`, `${mac_address}`, ...) therefore collide
with Flux: Flux reads them as substitution variables and, because they are
not defined in cluster-config/cluster-secrets, fails the whole Kustomization:

    post build failed for 'ConfigMap/esphome-devices':
    envsubst error: variable not set (strict mode): "name"

`kubectl kustomize` does NOT run envsubst, so CI stays green while the live
Kustomization is wedged — and every later change to it is blocked until
someone notices. This bit the repo twice: #15068 (the initial git-track) and
#15070 (a Renovate `@tag` bump merged green on top of the break). Fixed in
#15071.

The fix is to escape ESPHome substitutions as `$${var}`, which envsubst
emits as a literal `${var}` for ESPHome to resolve at compile time.

Scope, deliberately narrow so this stays zero-false-positive:
  * Only files passed on the command line (wired to the esphome resources
    dir). These files are configMap payloads, never k8s manifests.
  * Only the BRACED `${var}` form, and only when `var` contains a lowercase
    letter. ESPHome substitutions are lowercase; Flux cluster vars follow
    the SCREAMING_CASE convention (`${SECRET_DOMAIN}`, `${SVC_*_ADDR}`), so
    an all-uppercase braced var is assumed intentional and is NOT flagged —
    an ESPHome config that genuinely wants a Flux substitution can still use
    one.
  * `$${var}` (already escaped) is fine and is what this lint asks for.

Usage:
    tools/lint-esphome-config-substitutions.py [PATH ...]

If no PATHs are given, scans kubernetes/apps/home/esphome/app/resources/.
Supports a directory or individual files. Exits 0 if clean, 1 if any
unescaped lowercase substitution is found.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_ROOT = Path("kubernetes/apps/home/esphome/app/resources")

# A `$` NOT preceded by another `$`, followed by {IDENTIFIER}. The negative
# lookbehind distinguishes ${var} (eaten by envsubst) from $${var} (survives
# as a literal ${var} for ESPHome).
UNESCAPED_BRACED = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
HAS_LOWER = re.compile(r"[a-z]")


def lint_file(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    errors: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in UNESCAPED_BRACED.finditer(line):
            name = match.group(1)
            if not HAS_LOWER.search(name):
                continue  # SCREAMING_CASE → assume a Flux cluster var
            errors.append(
                f"{path}:{lineno}: unescaped ESPHome substitution ${{{name}}} "
                f"— Flux postBuild envsubst runs in strict mode over the "
                f"esphome-devices ConfigMap and will fail the Kustomization. "
                f"Write it as $${{{name}}} instead."
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
        help=f"Files or directories to scan (default: {DEFAULT_ROOT})",
    )
    args = parser.parse_args(argv)

    files = find_yaml_files(args.paths or [DEFAULT_ROOT])

    all_errors: list[str] = []
    for f in files:
        all_errors.extend(lint_file(f))

    if all_errors:
        print(
            "ERROR: unescaped ESPHome ${var} substitution(s) found — Flux "
            "postBuild envsubst runs in strict mode over the esphome-devices "
            "ConfigMap and will break reconciliation of the whole "
            "Kustomization (kubectl kustomize does not catch this):",
            file=sys.stderr,
        )
        for msg in all_errors:
            print(f"  {msg}", file=sys.stderr)
        print(
            f"\n{len(all_errors)} unescaped substitution(s) found. Escape "
            f"them as $${{var}}; see #15071 and "
            f"tools/lint-envsubst-shell-vars.py for the same class of bug.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
