---
name: wiki
description: "Maintain Rob's LLM-Wiki — a persistent, compounding knowledge base in the Obsidian vault (raw sources in, cross-linked synthesis out). Ingest / query / lint workflows. Local Hermes; the Librarian profile owns it."
version: 1.0.0
author: Hermes Agent (home-ops)
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [Wiki, KnowledgeBase, Obsidian, Synthesis, Ingest, Lint]
---

# LLM-Wiki

A persistent, LLM-maintained knowledge base (the Karpathy "LLM wiki" pattern). Rob curates *raw sources*;
you maintain a *compounding wiki* of cross-linked markdown synthesis. The wiki is the artifact — the
cross-references are already there and the synthesis already reflects everything read, so a later question
is answered from the wiki, not by re-reading raw docs. **You do the bookkeeping; Rob does the thinking.**

It lives in the Obsidian vault (`$OBSIDIAN_VAULT_PATH` = `/vault`), so every page you write syncs to Rob's
Obsidian on his laptop via the livesync bridge — bidirectionally. He edits in Obsidian; you edit here;
CouchDB reconciles.

## Layout (the three layers)

```
/vault/
├── raw/                     # LAYER 1 — sources Rob curates. READ ONLY. Never edit.
│   └── assets/              #   images / PDFs / attachments
└── wiki/                    # LAYER 2 — pages you write
    ├── SCHEMA.md            #   structure + conventions (this wiki's instruction manual; co-evolves)
    ├── index.md             #   catalog of every page, by category, with one-line descriptions
    ├── log.md               #   chronological activity record (append-only)
    ├── overview.md          #   the running synthesis / "what we know so far"
    ├── sources/             #   one summary page per raw source
    ├── entities/            #   people, orgs, products, places
    ├── concepts/            #   theories, methods, definitions
    └── analyses/            #   comparisons, syntheses, answers worth keeping
```

Cross-link everything with Obsidian wikilinks: `[[entities/acme-corp]]`, `[[concepts/raft-consensus]]`.
A `[[link]]` to a page that doesn't exist yet is fine — it marks a page worth writing (an intentional
stub), the same convention the vault already uses.

## Bookkeeping helper

`python3 /opt/data/skills/wiki/scripts/wiki.py <cmd>` handles the deterministic parts so you don't
hand-maintain them (reads `$OBSIDIAN_VAULT_PATH`):

- `wiki.py init` — create the layout (idempotent): the subdirs + skeleton `SCHEMA.md`/`index.md`/`log.md`/
  `overview.md` if missing. Run once to bootstrap.
- `wiki.py log "<operation> | <details>"` — append `## [YYYY-MM-DD HH:MM] <operation> | <details>` to
  `wiki/log.md` (Eastern time). Call after every ingest / material edit.
- `wiki.py lint` — health report: orphan pages (no inbound `[[links]]`), broken/stub links, pages missing
  from `index.md`, and `index.md` entries whose file is gone. Prints a structured summary; you decide what
  to fix.
- `wiki.py reindex` — rebuild `wiki/index.md` from the files on disk (category → page → first-line
  description). Use after a batch of edits, then eyeball the diff.

The helper never deletes or rewrites page *content* — it only touches `index.md`/`log.md` structure. All
synthesis is yours.

## Ingest workflow (a new source)

Triggered by a board card ("ingest `raw/<file>`") or Rob pointing you at a file.

1. **Read** the source in `/vault/raw/`. Discuss the key takeaways with Rob first if this is interactive.
2. **Summarize** → write `wiki/sources/<slug>.md`: citation header (title, author, date, `raw/` path), a
   tight summary, and the key claims — each claim cross-linked to the entity/concept pages it touches.
3. **Propagate** → revise the relevant `entities/` and `concepts/` pages so they reflect this source
   (create them if new). Expect to touch ~10–15 pages for a rich source — that propagation IS the value.
4. **Contradictions** → if a claim conflicts with an existing page, FLAG both with exact citations
   (`> ⚠️ Contradiction: [[sources/a]] says X; [[sources/b]] says Y`); do not silently overwrite.
   `kanban_block` for Rob if it's material.
5. **Index + overview** → add the new pages to `index.md` (or `wiki.py reindex`); fold anything that
   shifts the big picture into `overview.md`.
6. **Log** → `wiki.py log "ingest | <slug> (<n> pages touched)"`.

## Query workflow (answer a question)

1. Search `index.md` for relevant pages; read them (not the raw docs).
2. Synthesize an answer **with citations** to the wiki pages (and through them, the sources).
3. If the answer is durable and reusable, file it as `wiki/analyses/<slug>.md`, link it from `index.md`,
   and `wiki.py log "analysis | <slug>"` — so explorations compound instead of vanishing into chat.

## Lint workflow (periodic health-check)

Run `wiki.py lint`, then act on what it surfaces: fix orphans (add inbound links or fold them in), resolve
stubs (write the stub or drop the link), reconcile flagged contradictions with Rob, and fill obvious gaps.
Finish with `wiki.py reindex` and `wiki.py log "lint | <what changed>"`.

## Boundaries

- `raw/` is Rob's; never edit or delete a source. `wiki/` is yours; everything outside `raw/` + `wiki/`
  in the vault is off-limits (see the Librarian SOUL).
- Local only. Do not send vault content that originated from LightRAG or a restricted / medical note to
  `claude -p` — the gate blocks it; don't route around it.
- Optional cross-feed to LightRAG (`lightrag` skill `ingest`) is **off by default** — the wiki is the
  curated synthesis layer; LightRAG is automated retrieval. Only cross-feed a finished page when Rob asks.
