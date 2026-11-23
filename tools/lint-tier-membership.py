#!/usr/bin/env python3
"""Every namespace dir under kubernetes/apps belongs to exactly one Flux parent.

The parents' roots are kubernetes/apps/kustomization.yaml (the cluster-apps
parent, bare dir names) and kubernetes/tiers/*/kustomization.yaml (one per tier
parent, `../../apps/<dir>` entries). A dir that no root lists is silently
unmanaged: its objects stay live but never receive another Git change. A dir
that two roots list makes both parents fight over its owner labels, and the
next removal from one of them deletes based on whichever label is live.

A dir may instead be disabled: commented out (`# - <dir>`) in exactly one root
and listed in none. A root may list only whole namespace dirs: an entry that
points below one (`../../apps/home/esphome`) would let two parents render the
same Kustomization, so it is an error.

Usage: lint-tier-membership.py [REPO_ROOT]   (default: .)
"""

import pathlib
import re
import sys

ENTRY = re.compile(r"^\s*(#\s*)?-\s+(\S+)\s*$")


def entries(root: pathlib.Path, kfile: pathlib.Path, errors: list):
    """Yield (dir, active) for every resources entry that names an apps dir."""
    in_resources = False
    for line in kfile.read_text().splitlines():
        if re.match(r"^resources:\s*$", line):
            in_resources = True
            continue
        if in_resources and re.match(r"^[A-Za-z_]", line):
            in_resources = False
        if not in_resources:
            continue
        m = ENTRY.match(line)
        if not m:
            continue
        target = (kfile.parent / m.group(2)).resolve()
        apps = (root / "kubernetes/apps").resolve()
        if target.parent != apps:
            if apps in target.parents and m.group(1) is None:
                errors.append(f"{kfile}: `{m.group(2)}` points inside a namespace dir; roots may list only whole kubernetes/apps/<dir> entries")
            continue
        yield target.name, m.group(1) is None


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    apps = root / "kubernetes/apps"
    roots = [apps / "kustomization.yaml"] + sorted((root / "kubernetes/tiers").glob("*/kustomization.yaml"))
    dirs = {p.name for p in apps.iterdir() if p.is_dir()}
    active: dict[str, list[str]] = {}
    disabled: dict[str, list[str]] = {}
    errors = []
    for kfile in roots:
        rel = str(kfile.relative_to(root))
        for name, is_active in entries(root, kfile, errors):
            (active if is_active else disabled).setdefault(name, []).append(rel)
    for name in sorted(set(active) - dirs):
        errors.append(f"{', '.join(active[name])}: lists `{name}`, which is not a dir under kubernetes/apps")
    for name in sorted(dirs):
        a, d = active.get(name, []), disabled.get(name, [])
        if len(a) > 1:
            errors.append(f"kubernetes/apps/{name}: listed by more than one parent root: {', '.join(a)}")
        elif not a and len(d) != 1:
            errors.append(
                f"kubernetes/apps/{name}: listed by no parent root"
                + (f" (commented out in {', '.join(d)})" if d else "")
                + "; add it to exactly one root, or comment it out in exactly one"
            )
        elif a and d:
            errors.append(f"kubernetes/apps/{name}: active in {a[0]} but also commented out in {', '.join(d)}")
    for e in errors:
        print(e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
