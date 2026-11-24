#!/usr/bin/env python3
"""Reject changes under the tier roots while a move window is open.

A move between Flux tier parents (docs/src/gitops_layout.md, "Moving a dir
between parents") runs with every involved parent at `prune: false`, marked by
the annotation `home-ops.io/move-window: "<PR#>"`. During the window, anything
removed from a root is dropped from its parent's inventory instead of being
garbage-collected, so it stays in the cluster unmanaged. The runbook freezes
other merges for that reason. During the tier split three unrelated merges
landed inside the freeze; all were additive, but a removal would have leaked.

If any parent at the base revision carries the annotation, a change touching
kubernetes/apps/ or kubernetes/tiers/ fails, unless the PR description
contains the line `Move-Window: <PR#>` naming that open window (the move PR
itself, or a correction inside the window). Changes to the parents
(kubernetes/flux/cluster/), including the PR that closes the window, are
not affected.

Branch protection does not require branches to be up to date, so this only
gates PRs whose CI runs while the window is open. The runbook says to re-run
Lint on open PRs once a window opens. A re-run keeps the original merge
commit, so CI reads the window state from a fresh fetch of main
(--window-ref), not from the PR's base.

Usage:
    tools/lint-move-window-freeze.py [--base REF] [--head REF] [--window-ref REF]

--base defaults to origin/main, --head to HEAD; the diff between them is the
change under test. --window-ref (default: --base) is where the parents'
annotations are read. The PR description is fetched
from the API when $PR_NUMBER, $GITHUB_REPOSITORY and $GITHUB_TOKEN are set (so
a re-run sees an edited description; the event payload would not), else read
from $PR_BODY. Exits 0 if clean or no window is open, 1 on a violation, and
2 if the base parents cannot be read.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

PARENTS = "kubernetes/flux/cluster/ks.yaml"
FROZEN = ("kubernetes/apps/", "kubernetes/tiers/")
ANNOTATION = "home-ops.io/move-window"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def open_windows(base: str) -> dict[str, str]:
    """Parent name -> window PR number, for every annotated parent at base."""
    out = {}
    for doc in yaml.safe_load_all(git("show", f"{base}:{PARENTS}")):
        meta = (doc or {}).get("metadata") or {}
        value = (meta.get("annotations") or {}).get(ANNOTATION)
        if value is not None:
            out[meta.get("name", "?")] = str(value).lstrip("#")
    return out


def pr_body() -> str:
    number, repo, token = (os.environ.get(k) for k in ("PR_NUMBER", "GITHUB_REPOSITORY", "GITHUB_TOKEN"))
    if not (number and repo and token):
        return os.environ.get("PR_BODY", "")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/pulls/{number}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get("body") or ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--window-ref")
    args = ap.parse_args()
    window_ref = args.window_ref or args.base

    try:
        windows = open_windows(window_ref)
    except (subprocess.CalledProcessError, yaml.YAMLError) as e:
        print(f"ERROR: cannot read {PARENTS} at {window_ref}: {e}", file=sys.stderr)
        return 2
    if not windows:
        print("No move window open.")
        return 0

    changed = git("diff", "--name-only", args.base, args.head).split()
    frozen = [f for f in changed if f.startswith(FROZEN)]
    numbers = set(windows.values())
    print(f"Move window open: {', '.join(f'{p} (#{n})' for p, n in sorted(windows.items()))}")
    if not frozen:
        print("This change touches no tier root.")
        return 0

    claimed = set(re.findall(r"(?mi)^\s*Move-Window:\s*#?(\d+)\s*$", pr_body()))
    if claimed & numbers:
        print(f"Exempt: the PR description names window #{', #'.join(sorted(claimed & numbers))}.")
        return 0

    print(f"ERROR: {len(frozen)} file(s) under the tier roots change while the window is open:", file=sys.stderr)
    for f in frozen[:20]:
        print(f"  {f}", file=sys.stderr)
    print(
        "Merge this after the window closes (every parent back to prune: true). If this PR is\n"
        f"part of the window, add the line `Move-Window: {min(numbers)}` to its description and\n"
        "re-run the job. See docs/src/gitops_layout.md.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
