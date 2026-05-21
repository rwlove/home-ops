#!/usr/bin/env python3
"""Reject README.md when its badges/body counts disagree with repo reality.

The repo's `CLAUDE.md` says "Docs update with the change. Repo README.md
and docs/ are updated in the same PR as the change they describe. Stale
docs are bugs." This check makes that enforceable for the README's
mechanically-countable claims.

What it asserts (badge-style, auto-fixable):

  apps badge          == number of dirs at kubernetes/apps/<group>/<app>/
  HelmReleases badge  == number of files declaring `kind: HelmRelease` under kubernetes/
  Postgres badge      == number of `postgresql.cnpg.io` `kind: Cluster` declarations
  secrets badge       == number of `kind: ExternalSecret` declarations in kubernetes/apps/
  nodes badge         == number of data rows in the Hardware table

The node count is self-consistency only (CI can't reach the cluster) —
it ensures a new hardware row is paired with a badge bump.

What it also asserts (narrative drift, manual-fix only):

  MCP servers count   — the "🔌 MCP Servers — N …" summary line must match
                        the MCPServerRegistration count in kubernetes/
  Windmill workflows  — "(\\d+) TypeScript flows" claims in the README must
                        match the *.ts file count under
                        kubernetes/apps/home/windmill/workflows/
  Namespace paths     — every `kubernetes/apps/<group>/<app>/` path
                        mentioned in the README must exist on disk
  Mermaid hygiene     — blocks must not mention `n8n` (retired during the
                        ntfy migration); blocks with an `Inference`
                        subgraph must mention `ollama-spark`

Usage:
    tools/lint-readme-drift.py [README_PATH]
    tools/lint-readme-drift.py --fix [README_PATH]

Exits 0 if everything passes, 1 if any drift.

With `--fix`, badge drift is corrected in-place. Narrative drift is
never auto-fixable (it requires operator judgment about which prose to
update) — if any narrative check fails the script still exits 2.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APPS_DIR = REPO_ROOT / "kubernetes" / "apps"
KUBERNETES_DIR = REPO_ROOT / "kubernetes"
WINDMILL_WORKFLOWS_DIR = (
    REPO_ROOT / "kubernetes" / "apps" / "home" / "windmill" / "workflows"
)

BADGE_RE = re.compile(
    r"!\[(?P<name>[^\]]+)\]\(https://img\.shields\.io/badge/"
    r"(?P<label>[^-]+)-(?P<value>[^-]+)-",
)
MERMAID_BLOCK_RE = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)
NAMESPACE_PATH_RE = re.compile(
    r"kubernetes/apps/([a-z0-9][a-z0-9-]*)/([a-z0-9][a-z0-9-]*)(?:/|\b)"
)
MCP_SUMMARY_RE = re.compile(
    r"<b>MCP Servers</b>\s*[—\-]\s*(\d+)\s+Model Context Protocol"
)
WINDMILL_COUNT_RE = re.compile(
    r"(\d+)\s+(?:checked-in\s+)?(?:TypeScript|TS)\s+(?:flows?|workflows?)",
    re.IGNORECASE,
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


def check_mcp_count(readme_text: str) -> list[str]:
    """The README's MCP-section summary line must agree with the
    MCPServerRegistration count in kubernetes/.

    Returns a list of drift messages (empty list = no drift).
    """
    actual = count_kind("MCPServerRegistration", KUBERNETES_DIR)
    m = MCP_SUMMARY_RE.search(readme_text)
    if not m:
        return [
            f"MCP-servers summary line not found in README "
            f"(expected `🔌 MCP Servers — {actual} Model Context Protocol …`)"
        ]
    claimed = int(m.group(1))
    if claimed != actual:
        return [
            f"README claims {claimed} MCP servers; repo has {actual} "
            f"MCPServerRegistration kinds under kubernetes/"
        ]
    return []


def check_windmill_workflow_count(readme_text: str) -> list[str]:
    """Every `(\\d+) TypeScript flows` claim in the README must match the
    `*.ts` file count under kubernetes/apps/home/windmill/workflows/.

    Quiet (no drift) if the workflows directory doesn't exist yet or the
    README makes no count claim — this check is only for keeping an
    explicit number honest, not for forcing one to be written.
    """
    if not WINDMILL_WORKFLOWS_DIR.is_dir():
        return []
    actual = len(list(WINDMILL_WORKFLOWS_DIR.glob("*.ts")))
    matches = WINDMILL_COUNT_RE.findall(readme_text)
    if not matches:
        return []
    drifts: list[str] = []
    for claimed_str in matches:
        if int(claimed_str) != actual:
            drifts.append(
                f"README claims {claimed_str} Windmill TS workflows; "
                f"repo has {actual} .ts files under "
                f"kubernetes/apps/home/windmill/workflows/"
            )
    return drifts


def check_namespace_paths(readme_text: str) -> list[str]:
    """Every `kubernetes/apps/<group>/<app>/` path mentioned in README
    must exist on disk. Catches stale references like
    `kubernetes/apps/default/khoj/` after an app moves namespaces.
    """
    drifts: list[str] = []
    seen: set[tuple[str, str]] = set()
    for m in NAMESPACE_PATH_RE.finditer(readme_text):
        group, app = m.group(1), m.group(2)
        if (group, app) in seen:
            continue
        seen.add((group, app))
        if not (APPS_DIR / group / app).is_dir():
            drifts.append(
                f"README references kubernetes/apps/{group}/{app}/ "
                f"which doesn't exist on disk"
            )
    return drifts


def check_mermaid_sanity(readme_text: str) -> list[str]:
    """Lightweight textual lints on each ```mermaid``` block:

    - Block the literal `n8n` (retired during the ntfy migration; any
      remaining mention is stale).
    - Any block containing an `Inference` subgraph header must also
      mention `ollama-spark` (the post-Spark backend is the cluster's
      current workhorse).

    Not a full graph parser — just regex-grade sanity.
    """
    drifts: list[str] = []
    for i, block_match in enumerate(MERMAID_BLOCK_RE.finditer(readme_text), start=1):
        block = block_match.group(1)
        if re.search(r"\bn8n\b", block):
            drifts.append(
                f"mermaid block #{i} mentions `n8n` — retired during "
                f"the ntfy migration; update to Windmill"
            )
        if "Inference" in block and "ollama-spark" not in block.lower() \
                and "OllamaSpark" not in block:
            drifts.append(
                f"mermaid block #{i} has an Inference subgraph but "
                f"doesn't mention ollama-spark (post-Spark default)"
            )
    return drifts


def run_narrative_checks(readme_text: str) -> list[str]:
    """Run every non-badge check; return the flat list of drift lines."""
    drifts: list[str] = []
    drifts.extend(check_mcp_count(readme_text))
    drifts.extend(check_windmill_workflow_count(readme_text))
    drifts.extend(check_namespace_paths(readme_text))
    drifts.extend(check_mermaid_sanity(readme_text))
    return drifts


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

    narrative_drift = run_narrative_checks(text)

    if not drift and not narrative_drift:
        print("✅ README badges match repo reality:")
        for label, value in expectations.items():
            print(f"   {label}: {value}")
        print("✅ README narrative checks passed:")
        print("   MCP-server count, Windmill workflow count, "
              "namespace paths, mermaid hygiene")
        return 0

    if fix:
        # Auto-fix all badge drift except `k8s_nodes` — that one is
        # sourced from the README's own Hardware table, so fixing the
        # badge would just paper over a stale table. Surface it
        # instead. Narrative drift is never auto-fixable.
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
            print("⚠️  Badge drift NOT auto-fixed (requires manual edit):")
            for label, actual, expected in unfixed:
                print(f"   {label}: {actual} → {expected} (Hardware table or missing badge)")
        if narrative_drift:
            print("⚠️  Narrative drift NOT auto-fixed (requires operator edit):")
            for line in narrative_drift:
                print(f"   - {line}")
        return 2 if (unfixed or narrative_drift) else 0

    if drift:
        print("❌ README.md badges are drifting from repo reality:")
        for label, actual, expected in drift:
            print(f"  - badge `{label}` says `{actual}`; repo reality is `{expected}`")
        print()
    if narrative_drift:
        print("❌ README.md narrative is drifting from repo reality:")
        for line in narrative_drift:
            print(f"  - {line}")
        print()
    print("Update the README + any matching body text in the same PR")
    print("that changed the underlying state, per CLAUDE.md:")
    print('  "Repo README.md and docs/ are updated in the same PR as')
    print('   the change they describe. Stale docs are bugs."')
    print()
    if drift:
        print("For badge drift only: run `tools/lint-readme-drift.py --fix`.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
