"""MkDocs hooks for home-ops docs.

Ports the single entry in the legacy mdBook ``replace-patterns.json``:
drop the ``### 📖 Docs`` section from the repo README before it lands
in ``introduction.md`` via ``--8<-- "README.md"`` (the pymdownx.snippets
include). The repo README points readers at the docs site; when the
README is itself embedded in the docs site, the pointer is a loop.
"""

from __future__ import annotations

import re

_DOCS_SECTION_RE = re.compile(r"\n### 📖 Docs[\s\S]*?---\n")


def on_page_markdown(markdown: str, *, page, config, files) -> str:
    if page.file.src_uri == "introduction.md":
        return _DOCS_SECTION_RE.sub("", markdown)
    return markdown
