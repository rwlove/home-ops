#!/usr/bin/env python3
"""Reject README.md when its badges/body counts disagree with repo reality.

The repo's `CLAUDE.md` says "Docs update with the change. Repo README.md
and docs/ are updated in the same PR as the change they describe. Stale
docs are bugs." This check makes that enforceable for the README's
mechanically-countable claims.

What it asserts:

  apps badge          == number of dirs at kubernetes/apps/<group>/<app>/
  HelmReleases badge  == number of files declaring `kind: HelmRelease` under kubernetes/
  Postgres badge      == number of `postgresql.cnpg.io` `kind: Cluster` declarations
  secrets badge       == number of `kind: ExternalSecret` declarations in kubernetes/apps/
  nodes badge         == number of data rows in the Hardware table

The node count is self-consistency only (CI can't reach the cluster) —
it ensures a new hardware row is paired with a badge bump.

Usage:
    tools/lint-readme-drift.py [README_PATH]
    tools/lint-readme-drift.py --fix [README_PATH]

Exits 0 if all badges match, 1 if any drift.

With `--fix`, drift is corrected in-place and the script exits 0
(or 2 if the only drift is the `k8s_nodes` badge — that one is
operator-edited alongside the Hardware table, not auto-derivable from
code, so we refuse to auto-rewrite it).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APPS_DIR = REPO_ROOT / "kubernetes" / "apps"
KUBERNETES_DIR = REPO_ROOT / "kubernetes"

BADGE_RE = re.compile(
    r"!\[(?P<name>[^\]]+)\]\(https://img\.shields\.io/badge/"
    r"(?P<label>[^-]+)-(?P<value>[^-]+)-",
)


def count_app_dirs() -> int:
    """Top-level app dirs at kubernetes/apps/<group>/<app>/."""
    return sum(
        1
        for group in APPS_DIR.iterdir()
        if group.is_dir()
        for app in group.iterdir()
        if app.is_dir()
    )


def count_kind(kind: str, scope: Path, *, api_group: str | None = None) -> int:
    """Count YAML documents whose top-level `kind:` matches `kind`.

    Only matches `kind:` at column 0 — indented `kind:` references
    (kustomize targets, Flux dependsOn entries, notification provider
    event filters) are not declarations and don't count.

    If `api_group` is given, the file must also reference that string
    (so e.g. `kind: Cluster` only counts when the file mentions
    `postgresql.cnpg.io`).
    """
    needle = f"kind: {kind}"
    total = 0
    for path in scope.rglob("*.yaml"):
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        if api_group and api_group not in text:
            continue
        total += sum(1 for line in text.splitlines() if line == needle)
    return total


def count_hardware_rows(readme_text: str) -> int:
    """Count data rows in the Hardware table.

    The table starts after `## 🖥️ Hardware` and ends at the next
    `###` or `---`. Data rows begin with `|` and aren't the header
    or the separator (`|---|...`).
    """
    in_hw = False
    rows = 0
    for line in readme_text.splitlines():
        if line.startswith("## ") and "Hardware" in line:
            in_hw = True
            continue
        if not in_hw:
            continue
        if line.startswith("### ") or line.startswith("---"):
            break
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        # Skip header (contains `Role` / `Hostname`) and separator (`|---`)
        if "Hostname" in stripped or set(stripped) <= set("|-: "):
            continue
        rows += 1
    return rows


def parse_badges(readme_text: str) -> dict[str, str]:
    """Return {label: value} for every shields.io badge in README."""
    out: dict[str, str] = {}
    for m in BADGE_RE.finditer(readme_text):
        out[m.group("label")] = m.group("value")
    return out


def rewrite_badge(text: str, label: str, new_value: str) -> str:
    """Replace the value in a shields.io badge for `label` with `new_value`.

    Operates on the exact pattern `/badge/<label>-<value>-` so we don't
    touch the rest of the URL (color, style flags, logo, etc.).
    """
    pattern = re.compile(
        rf"(/badge/{re.escape(label)}-)([^-]+)(-)",
    )
    return pattern.sub(rf"\g<1>{new_value}\g<3>", text, count=1)


def main(argv: list[str]) -> int:
    args = argv[1:]
    fix = False
    if args and args[0] == "--fix":
        fix = True
        args = args[1:]

    readme_path = Path(args[0]) if args else REPO_ROOT / "README.md"
    text = readme_path.read_text()
    badges = parse_badges(text)

    expectations = {
        "apps": str(count_app_dirs()),
        "HelmReleases": str(count_kind("HelmRelease", KUBERNETES_DIR)),
        "Postgres_clusters": str(
            count_kind("Cluster", KUBERNETES_DIR, api_group="postgresql.cnpg.io")
        ),
        "secrets": str(count_kind("ExternalSecret", APPS_DIR)),
        "k8s_nodes": str(count_hardware_rows(text)),
    }

    drift: list[tuple[str, str, str]] = []
    for label, expected in expectations.items():
        actual = badges.get(label)
        if actual is None:
            drift.append((label, "<missing>", expected))
        elif actual != expected:
            drift.append((label, actual, expected))

    if not drift:
        print("✅ README badges match repo reality:")
        for label, value in expectations.items():
            print(f"   {label}: {value}")
        return 0

    if fix:
        # Auto-fix all drift except `k8s_nodes` — that one is sourced
        # from the README's own Hardware table, so fixing the badge
        # would just paper over a stale table. Surface it instead.
        unfixed: list[tuple[str, str, str]] = []
        new_text = text
        fixed = []
        for label, actual, expected in drift:
            if label == "k8s_nodes":
                unfixed.append((label, actual, expected))
                continue
            if actual == "<missing>":
                unfixed.append((label, actual, expected))
                continue
            new_text = rewrite_badge(new_text, label, expected)
            fixed.append((label, actual, expected))

        if fixed:
            readme_path.write_text(new_text)
            print("🛠  README badges auto-fixed:")
            for label, actual, expected in fixed:
                print(f"   {label}: {actual} → {expected}")
        if unfixed:
            print("⚠️  Drift NOT auto-fixed (requires manual edit):")
            for label, actual, expected in unfixed:
                print(f"   {label}: {actual} → {expected} (Hardware table or missing badge)")
            return 2
        return 0

    print("❌ README.md is drifting from repo reality:")
    for label, actual, expected in drift:
        print(f"  - badge `{label}` says `{actual}`; repo reality is `{expected}`")
    print()
    print("Update the badges + any matching body text in the same PR")
    print("that changed the underlying counts, per CLAUDE.md:")
    print('  "Repo README.md and docs/ are updated in the same PR as')
    print('   the change they describe. Stale docs are bugs."')
    print()
    print("Or run `tools/lint-readme-drift.py --fix` to auto-correct.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
