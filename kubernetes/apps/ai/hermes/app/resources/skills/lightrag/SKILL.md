---
name: lightrag
description: "Query and add to Rob's LightRAG knowledge graph (personal docs: finances, property, tax, contracts). LOCAL Hermes only — never forward its content to claude -p."
version: 1.0.0
author: Hermes Agent (home-ops)
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [RAG, KnowledgeGraph, LightRAG, Search, Memory, Ingest]
---

# LightRAG Knowledge Graph

Query and extend Rob's private LightRAG knowledge graph — the entity/relationship graph built from his
personal documents (finances, property + contractor estimates, tax forms, contracts, etc.).

**Data boundary (hard rule):** this content is internal + **personal**. LOCAL Hermes (the 35B) may use it
freely. **Never** paste LightRAG results into a `claude -p` escalation or any remote-model prompt — it
must not leave the network (HOMELAB-SPEC L2 #1). The gate blocks LightRAG-marked context from `claude -p`;
do not route around it.

The endpoint URL and API key come from the environment (`LIGHTRAG_URL`, `LIGHTRAG_API_KEY`), already
injected — the helper scripts read them, so you never handle the key directly.

## When to use

- "What do we know about <topic> from my documents", "look up <account / estimate / form>", grounding an
  answer in Rob's records, or "add this finding to the knowledge graph".

## Query (retrieve grounded knowledge)

```
python3 scripts/query.py "your natural-language question" [mode]
```

- `mode` (optional): `local` (entity-centric, fastest — the default), `global` (relationship-centric),
  `mix`/`hybrid` (both, richest but slower), `naive` (plain vector).
- Add `--context-only` to get the raw retrieved KG (entities/relations/chunks) without LLM synthesis, to
  reason over the sources yourself.

## Ingest (add knowledge to the graph)

```
python3 scripts/ingest.py "the durable finding/text to add"
# or pipe:  echo "..." | python3 scripts/ingest.py
```

Ingest durable, factual findings (not transient chatter). Ingestion is async — LightRAG extracts
entities/relations on its own pipeline; nothing to wait on.
