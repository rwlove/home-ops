#!/usr/bin/env python3
"""LLM-Wiki bookkeeping helper — the deterministic parts so the Librarian doesn't
hand-maintain the index/log or eyeball orphans. Content synthesis stays with the
agent; this only touches structure.

Commands: init | log "<op> | <details>" | lint | reindex
Reads the vault root from OBSIDIAN_VAULT_PATH (default /vault). Stdlib only.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path

VAULT = Path(os.environ.get("OBSIDIAN_VAULT_PATH", "/vault"))
WIKI = VAULT / "wiki"
RAW = VAULT / "raw"
CATEGORIES = ("sources", "entities", "concepts", "analyses")
SPECIAL = {"SCHEMA.md", "index.md", "log.md", "overview.md"}
LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")


def _now() -> str:
    """Eastern timestamp (Rob's standing rule #7). Falls back to local if tzdata absent."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M")


def _pages() -> list[Path]:
    """All wiki page files (relative-addressable), excluding the special top-level files."""
    if not WIKI.is_dir():
        return []
    out = []
    for p in sorted(WIKI.rglob("*.md")):
        rel = p.relative_to(WIKI)
        if len(rel.parts) == 1 and rel.name in SPECIAL:
            continue
        out.append(p)
    return out


def _rel(p: Path) -> str:
    return p.relative_to(WIKI).as_posix()[:-3]  # drop .md → "entities/acme-corp"


def _resolve(target: str, pages: list[Path]) -> Path | None:
    """Resolve a [[wikilink]] target to a page. 'cat/slug' → exact; bare 'slug' → basename match."""
    target = target.strip()
    if "/" in target:
        cand = WIKI / (target + ".md")
        return cand if cand.exists() else None
    hits = [p for p in pages if p.stem == target]
    return hits[0] if len(hits) == 1 else None


def _links_in(p: Path) -> list[str]:
    try:
        return LINK_RE.findall(p.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []


def _delink(s: str) -> str:
    """Flatten `[[cat/slug|alias]]` → `alias`/`slug` so index descriptions stay plain text
    (and don't pollute the index-drift lint check)."""
    def repl(m: re.Match) -> str:
        inner = m.group(0)[2:-2]
        inner = inner.split("|")[-1] if "|" in inner else inner.split("/")[-1]
        return inner.split("#")[0]

    return re.sub(r"\[\[[^\]]+\]\]", repl, s)


def _first_desc(p: Path) -> str:
    """First non-empty, non-heading line → one-line (link-free) description for the index."""
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and not s.startswith(">"):
                return _delink(s)[:120]
    except OSError:
        pass
    return ""


def cmd_init() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "assets").mkdir(exist_ok=True)
    for c in CATEGORIES:
        (WIKI / c).mkdir(parents=True, exist_ok=True)
    skel = {
        "SCHEMA.md": (
            "# Wiki SCHEMA\n\n"
            "Structure + conventions for this LLM-Wiki (co-evolves with use). See the `wiki` skill for the\n"
            "ingest / query / lint workflows.\n\n"
            "## Layout\n\n"
            "- `raw/` — sources Rob curates (read-only).\n"
            "- `wiki/{sources,entities,concepts,analyses}/` — pages the Librarian writes.\n"
            "- `index.md` catalog · `log.md` activity · `overview.md` synthesis.\n\n"
            "## Conventions\n\n"
            "- Cross-link with `[[category/slug]]`. Unresolved links are intentional stubs.\n"
            "- Flag contradictions (`> ⚠️ Contradiction: …`) with citations; never silently overwrite.\n"
            "- Slugs are kebab-case. Every source page cites its `raw/` path.\n"
        ),
        "index.md": "# Wiki Index\n\n_Rebuild with `wiki.py reindex`._\n",
        "log.md": "# Wiki Log\n\nChronological activity record (append-only, Eastern time).\n",
        "overview.md": "# Overview\n\nThe running synthesis — what we know so far. Updated as sources land.\n",
    }
    created = []
    for name, body in skel.items():
        f = WIKI / name
        if not f.exists():
            f.write_text(body, encoding="utf-8")
            created.append(name)
    print(f"init: layout ready at {WIKI}")
    print("created:", ", ".join(created) if created else "(nothing new — already bootstrapped)")
    return 0


def cmd_log(msg: str) -> int:
    if not msg.strip():
        print("log: empty message", file=sys.stderr)
        return 2
    logf = WIKI / "log.md"
    if not logf.exists():
        cmd_init()
    with logf.open("a", encoding="utf-8") as fh:
        fh.write(f"\n## [{_now()}] {msg.strip()}\n")
    print(f"log: appended → {logf.relative_to(VAULT)}")
    return 0


def cmd_lint() -> int:
    pages = _pages()
    if not pages:
        print("lint: no wiki pages yet (run `wiki.py init` then ingest a source).")
        return 0
    # Build inbound-link map + collect unresolved targets.
    inbound: dict[str, int] = {_rel(p): 0 for p in pages}
    stubs: set[str] = set()
    for p in pages:
        for tgt in _links_in(p):
            r = _resolve(tgt, pages)
            if r is not None:
                inbound[_rel(r)] = inbound.get(_rel(r), 0) + 1
            else:
                stubs.add(tgt.strip())
    orphans = [r for r, n in sorted(inbound.items()) if n == 0]

    # Index drift.
    idx = WIKI / "index.md"
    idx_text = idx.read_text(encoding="utf-8", errors="replace") if idx.exists() else ""
    idx_links = {t.strip() for t in LINK_RE.findall(idx_text)}
    missing_from_index = [_rel(p) for p in pages if _rel(p) not in idx_links and p.stem not in idx_links]
    dangling_index = [t for t in sorted(idx_links) if _resolve(t, pages) is None]

    def section(title: str, items: list[str]) -> None:
        print(f"\n{title} ({len(items)}):")
        for it in items[:40]:
            print(f"  - {it}")
        if len(items) > 40:
            print(f"  … +{len(items) - 40} more")

    print(f"lint: {len(pages)} pages across {sum((WIKI / c).is_dir() for c in CATEGORIES)} categories")
    section("orphan pages (no inbound [[links]])", orphans)
    section("unresolved / stub links", sorted(stubs))
    section("pages missing from index.md", missing_from_index)
    section("index.md entries with no file", dangling_index)
    print("\n(advisory only — nothing was changed. Fix, then `wiki.py reindex`.)")
    return 0


def cmd_reindex() -> int:
    pages = _pages()
    lines = ["# Wiki Index", "", "_Generated by `wiki.py reindex` — edit source pages, not this file._", ""]
    for c in CATEGORIES:
        cat_pages = [p for p in pages if p.relative_to(WIKI).parts[0] == c]
        if not cat_pages:
            continue
        lines.append(f"## {c}")
        lines.append("")
        for p in sorted(cat_pages):
            desc = _first_desc(p)
            lines.append(f"- [[{_rel(p)}]]" + (f" — {desc}" if desc else ""))
        lines.append("")
    # Any pages outside the known categories.
    other = [p for p in pages if p.relative_to(WIKI).parts[0] not in CATEGORIES]
    if other:
        lines += ["## other", ""]
        lines += [f"- [[{_rel(p)}]]" for p in sorted(other)] + [""]
    (WIKI / "index.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"reindex: {len(pages)} pages → wiki/index.md")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "init":
        return cmd_init()
    if cmd == "log":
        return cmd_log(" ".join(sys.argv[2:]))
    if cmd == "lint":
        return cmd_lint()
    if cmd == "reindex":
        return cmd_reindex()
    print(f"unknown command: {cmd}\n{__doc__}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
