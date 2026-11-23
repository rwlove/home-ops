#!/usr/bin/env python3
"""Reject removing a ConfigMap/Secret key that the base revision mounts via subPath.

Removing (or renaming) a generated ConfigMap/Secret key in the same change
that drops its `subPath` mount takes the workload down. The two halves land
at different moments:

  1. The app's Kustomization applies the ConfigMap first; the key is gone.
  2. Reloader sees the ConfigMap change and restarts the pod on the OLD pod
     spec, which still mounts the missing key. Over a PVC-backed path that is
     a hard `CreateContainerError: ... Not a directory`; elsewhere it is a
     silent empty directory.
  3. The Helm upgrade carrying the new spec times out: a StatefulSet will not
     replace a pod that is stuck not-Ready.
  4. Helm remediation rolls back to the old spec, which cannot start either,
     and loops.

This took the ESPHome dashboard down for ~17 minutes on 2026-11-22
(home-ops#16357, retiring bluetooth-proxy-gym).

The safe order is two PRs:
  PR 1: remove the subPath mount (the key still exists, so the old and new
        pod specs both start).
  PR 2: remove the key / generator file (nothing mounts it any more).

Scope: for each app directory (a directory holding a kustomization.yaml)
touched by the diff, collect the keys provided by its configMapGenerator /
secretGenerator entries and by literal ConfigMap/Secret manifests in that
directory, at the base and head revisions. A key present at base, absent at
head, and used as a `subPath` value anywhere in that directory's manifests at
base is a violation. Matching is by key name within the app directory, which
errs towards flagging; the two-PR order always satisfies it. Deleting a whole
app directory is exempt (the workload is pruned with its ConfigMap).

Over the 400 main commits before this lint landed it flags exactly two:
#16357 (the outage) and #16189 (the same ESPHome pattern a day earlier,
which won the race).

Usage:
    tools/lint-subpath-key-removal.py [--base REF] [--head REF]

--base defaults to the merge-base of HEAD and origin/main. --head defaults to
the working tree. Exits 0 if clean, 1 on a violation, and 0 with a notice if
the base cannot be resolved (CI still runs it against the PR base).
"""
from __future__ import annotations

import argparse
import posixpath
import subprocess
import sys

try:
    import yaml
except ImportError:
    print(
        "ERROR: PyYAML is required. Install with: pip install pyyaml",
        file=sys.stderr,
    )
    sys.exit(2)

ROOT = "kubernetes"


class _Loader(yaml.SafeLoader):
    """SafeLoader that tolerates custom tags (e.g. ESPHome `!secret`)."""


_Loader.add_multi_constructor("", lambda loader, suffix, node: None)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


class Rev:
    """Read files at a git ref, or from the working tree when ref is None."""

    def __init__(self, ref: str | None):
        self.ref = ref

    def read(self, path: str) -> str | None:
        try:
            if self.ref is None:
                with open(path, encoding="utf-8") as fh:
                    return fh.read()
            return git("show", f"{self.ref}:{path}")
        except (OSError, subprocess.CalledProcessError, UnicodeDecodeError):
            return None

    def listdir(self, d: str) -> list[str]:
        if self.ref is None:
            import os

            try:
                return [
                    n for n in os.listdir(d) if os.path.isfile(posixpath.join(d, n))
                ]
            except OSError:
                return []
        try:
            out = git("ls-tree", "--name-only", f"{self.ref}:{d}")
        except subprocess.CalledProcessError:
            return []
        return [n for n in out.splitlines() if n]

    def docs(self, path: str) -> list:
        text = self.read(path)
        if text is None:
            return []
        try:
            return [d for d in yaml.load_all(text, Loader=_Loader) if d]
        except yaml.YAMLError:
            return []


def generator_keys(rev: Rev, appdir: str) -> set[str]:
    keys: set[str] = set()
    for kust in rev.docs(posixpath.join(appdir, "kustomization.yaml")):
        if not isinstance(kust, dict):
            continue
        for gen in (kust.get("configMapGenerator") or []) + (
            kust.get("secretGenerator") or []
        ):
            if not isinstance(gen, dict):
                continue
            for f in gen.get("files") or []:
                f = str(f)
                keys.add(f.split("=", 1)[0] if "=" in f else posixpath.basename(f))
            for lit in gen.get("literals") or []:
                keys.add(str(lit).split("=", 1)[0])
            for env in gen.get("envs") or []:
                text = rev.read(posixpath.normpath(posixpath.join(appdir, str(env))))
                for line in (text or "").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        keys.add(line.split("=", 1)[0])
    return keys


def manifest_docs(rev: Rev, appdir: str):
    for name in rev.listdir(appdir):
        if name.endswith((".yaml", ".yml")) and name != "kustomization.yaml":
            yield from rev.docs(posixpath.join(appdir, name))


def literal_keys(rev: Rev, appdir: str) -> set[str]:
    keys: set[str] = set()
    for doc in manifest_docs(rev, appdir):
        if isinstance(doc, dict) and doc.get("kind") in ("ConfigMap", "Secret"):
            for field in ("data", "stringData", "binaryData"):
                keys.update(str(k) for k in (doc.get(field) or {}))
    return keys


def subpaths(rev: Rev, appdir: str) -> set[str]:
    found: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "subPath" and isinstance(v, str):
                    found.add(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for doc in manifest_docs(rev, appdir):
        walk(doc)
    return found


def appdir_for(rev: Rev, path: str) -> str | None:
    d = posixpath.dirname(path)
    while d and (d == ROOT or d.startswith(ROOT + "/")):
        if "kustomization.yaml" in rev.listdir(d):
            return d
        d = posixpath.dirname(d)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", help="base ref (default: merge-base HEAD origin/main)")
    ap.add_argument("--head", help="head ref (default: working tree)")
    args = ap.parse_args()

    base = args.base
    if base is None:
        try:
            base = git("merge-base", "HEAD", "origin/main").strip()
        except subprocess.CalledProcessError:
            print("subpath-key-removal: cannot resolve origin/main; skipping")
            return 0

    diff = ["diff", "--name-only", base] + ([args.head] if args.head else [])
    changed = [p for p in git(*diff).splitlines() if p.startswith(ROOT + "/")]

    old, new = Rev(base), Rev(args.head)
    appdirs = {d for p in changed if (d := appdir_for(old, p))}

    violations = []
    for appdir in sorted(appdirs):
        # Whole app removed: Flux prunes the workload with the ConfigMap, so
        # there is no old pod spec left to restart against the new data.
        if "kustomization.yaml" not in new.listdir(appdir):
            continue
        removed = (generator_keys(old, appdir) | literal_keys(old, appdir)) - (
            generator_keys(new, appdir) | literal_keys(new, appdir)
        )
        for key in sorted(removed & subpaths(old, appdir)):
            violations.append((appdir, key))

    if not violations:
        return 0

    print("ERROR: ConfigMap/Secret key removed while the base revision mounts it via subPath:\n")
    for appdir, key in violations:
        print(f"  {appdir}: key '{key}'")
    print(
        "\nLanding both halves in one change makes Reloader restart the pod on the"
        "\nold spec against the new ConfigMap, which deadlocks with Helm rollback"
        "\n(ESPHome outage 2026-11-22, #16357). Split it:"
        "\n  PR 1: remove the subPath mount, merge, confirm the pod rolled."
        "\n  PR 2: remove the key / generator file."
        "\nSee .agents/instructions/configmap.resources.instructions.md."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
