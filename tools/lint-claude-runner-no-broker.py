#!/usr/bin/env python3
"""Guard: claude-runner (or any unattended-CI-runner config) must never
reference the MCP broker.

Per HOMELAB-SPEC's no-per-tool-authz guardrail and the mcp-platform
persona charter: claude-runner (the self-hosted, unattended CI-runner
execution lane -- see .agents/instructions/claude-runner-routing.md) must
NEVER touch the Kuadrant mcp-gateway broker. The broker has no per-tool
authz -- reachability there is the full mutating surface of every
federated backend -- and an unattended loop is exactly the context where
that's unacceptable (claude-runner reaches a mutating MCP tool only via
the dedicated, narrowly-scoped pr-mcp path today, never the broker).

This is a NEGATIVE check, not a live probe, and it is deliberately scoped
to *paths* named claude-runner (a directory or filename component),
NOT to any file that merely mentions the word in prose. Several existing
files document this exact guardrail in a comment ("never the mcp-gateway
broker (unattended-loop rule, claude-runner-routing.md)") -- scanning
file *content* for both markers flags that documentation as a violation,
which is backwards. Scoping to the path means: no claude-runner app dir
exists today, so this check is a true no-op forward guard -- it starts
doing real work the moment someone adds `kubernetes/apps/*/claude-runner*/`
(or a `.github/workflows/claude-runner*.yaml`) and wires it to the broker.

Usage:
    tools/lint-claude-runner-no-broker.py [PATH ...]

If no PATHs are given, scans `kubernetes/` and `.github/`. Exits 0 if
clean, 1 if a claude-runner-named path is wired to the broker.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CLAUDE_RUNNER_PATH = re.compile(r"claude-runner", re.IGNORECASE)
BROKER_MARKERS = (
    re.compile(r"mcp\.\$\{SECRET_DOMAIN\}"),
    re.compile(r"X-Mcp-Virtualserver", re.IGNORECASE),
    re.compile(r"mcp-gateway-authelia-client"),
    re.compile(r"\bmcp-gateway\b"),
)


def find_files(roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        if root.is_file():
            found.append(root)
            continue
        if not root.is_dir():
            continue
        for ext in ("*.yaml", "*.yml"):
            found.extend(root.rglob(ext))
    return sorted(set(found))


def lint_file(path: Path) -> list[str]:
    if not CLAUDE_RUNNER_PATH.search(str(path)):
        return []  # scoped to claude-runner-named PATHS only -- see module docstring
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    hits = [m.pattern for m in BROKER_MARKERS if m.search(text)]
    if not hits:
        return []
    return [
        f"{path}: a claude-runner-named path references the MCP broker "
        f"({', '.join(hits)}) -- claude-runner must never reach the broker "
        f"unattended (no-per-tool-authz guardrail). If this is intentional, "
        f"that's a guardrail change to propose to Robert explicitly, not a "
        f"silent wiring addition."
    ]


def main(argv: list[str]) -> int:
    roots = [Path(a) for a in argv] or [Path("kubernetes"), Path(".github")]
    files = find_files(roots)

    all_errors: list[str] = []
    for f in files:
        all_errors.extend(lint_file(f))

    if all_errors:
        print("ERROR: claude-runner wired to the MCP broker:", file=sys.stderr)
        for msg in all_errors:
            print(f"  {msg}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
