# Memory MCP

`memory-mcp` is a knowledge-graph MCP server backed by Postgres + pgvector (vchord HNSW). It exposes entity / observation / relation tools to every MCP client in the cluster, so LangGraph agents, Claude Code sessions, Open WebUI tool callers, and HolmesGPT triage runs all share the same long-term memory substrate.

| | |
|---|---|
| **Source repo** | <https://github.com/rwlove/memory-mcp> |
| **Image** | `ghcr.io/rwlove/memory-mcp` (digest-pinned in the HelmRelease) |
| **Manifests** | `kubernetes/apps/mcp-system/memory-mcp/` |
| **Backend** | `postgres-langgraph-memory` CNPG cluster, schema `kg` |
| **Tool prefix at the gateway** | `memory_` |

## Architecture

```text
              ┌─ Claude Code (laptop) ──┐
              │  + markdown auto-memory │  (per-project, parallel)
              │  + memory_* MCP tools   │
              └──────────┬──────────────┘
                         │
LangGraph agents ────────┤ ←  MCPMemoryStore(BaseStore) writes
                         │     directly to kg.* via psycopg3
Open WebUI / HolmesGPT ──┤
                         │
                         ▼
            ┌──────────────────────────────┐
            │  mcp-gateway (Envoy + Istio) │
            └──────────────┬───────────────┘
                           │ memory_* (toolPrefix)
                           ▼
            ┌──────────────────────────────┐
            │  memory-mcp  (FastMCP)       │
            │  streamable-http :8070       │
            └──────────────┬───────────────┘
                           │ psycopg / asyncpg
                           ▼
            ┌──────────────────────────────┐
            │ postgres-langgraph-memory    │
            │ pgvector + vchordrq HNSW     │
            │ database: langgraph_memory   │
            │ schema:   kg                 │
            └──────────────────────────────┘
```

LangGraph agents bypass the MCP transport — they speak direct SQL to the same `kg.*` schema via `MCPMemoryStore(BaseStore)`. The two write paths converge on identical rows.

## Schema

Three tables under the `kg` schema:

```sql
kg.entities      (id, name UNIQUE, type, namespace, source JSONB,
                  created_at, updated_at)
kg.observations  (id, entity_id, content, embedding vector(768),
                  source JSONB, created_at, deleted_at)
kg.relations     (id, from_entity, to_entity, type, source JSONB,
                  created_at, UNIQUE (from_entity, to_entity, type))
```

Indexes: `kg_obs_embedding_hnsw` using `vchordrq (embedding vector_cosine_ops)` for semantic search; per-column indexes on entity type + namespace.

Embedding model is `nomic-embed-text` (768 dimensions). The model must be resident on Ollama — see [Ollama /api/embed reference](./offsite_recovery.md) for the pull procedure (`POST /api/pull` with `{"model": "nomic-embed-text", "stream": false}`).

**Soft delete only.** `memory_delete_observation` sets `deleted_at = now()`, never DROPs. The principle is "don't silently delete memories across namespaces" — observation history stays inspectable even after a logical delete.

## Tool surface (12 tools, prefix `memory_`)

| Tool | Purpose |
|------|---------|
| `create_entity` | Create node + initial observations |
| `add_observation` | Append observation to existing entity |
| `get_entity` | Fetch by name + (optionally) one-hop relations |
| `list_entities` | Enumerate, filterable by type / namespace |
| `update_entity` | Rename / retype / re-namespace; relations survive via id-based join |
| `update_observation` | Replace content + re-embed in place |
| `delete_observation` | Soft delete (`deleted_at`) |
| `create_relation` | Typed directed edge between two entities |
| `link` | Sugar for `create_relation` (default type `related_to`) |
| `search` | Hybrid (semantic via vchord cosine, keyword via ILIKE) |
| `graph_walk` | BFS to depth N (cap 4) from a start entity |
| `recent` | Most-recent observations, filterable by agent / since / entity |
| `create_entities` | Transactional bulk-create |
| `add_observations` | Transactional bulk-append |

## Namespace conventions

Two distinct namespacings live in this schema:

1. **`entities.namespace`** — caller-supplied free-form grouping (e.g. `infra`, `cluster`, `software`, `people`). Filter via `memory_list_entities(namespace_filter=...)` or `memory_search(namespace_filter=...)`.
2. **`langgraph/*` prefix** — entities created by `MCPMemoryStore` (the LangGraph BaseStore adapter) all live under `langgraph/{ns0}/{ns1}/...`, where the LangGraph `namespace: tuple[str, ...]` becomes the path. Their `type` is always `langgraph-store-item`. This keeps fleet writes visually separated from human-seeded entries (host/cnpg/service/etc.) when other agents query the graph.

## Provenance

Every write carries a `source` JSONB. Server stamps `at: ISO8601` if the caller didn't. Conventions:

| Caller | Minimum source |
|--------|----------------|
| Claude Code | `{"agent": "claude-code", "claude_namespace": "<encoded-cwd>"}` |
| LangGraph fleet (via MCPMemoryStore) | `{"agent": "langgraph", "store": "MCPMemoryStore"}` (set automatically) |
| Open WebUI tool calls | `{"agent": "open-webui"}` |
| HolmesGPT | `{"agent": "holmesgpt"}` |

A `memory_recent(agent_filter="claude-code")` query shows just one agent's recent contributions.

## Observability

`/metrics` exposes Prometheus scrape data:

- **Process / GC collectors** (CPU, RSS, fd count, GC pauses) — `prometheus_client` defaults.
- **`memory_mcp_tool_calls_total{tool, status}`** — counter per tool, status ∈ {success, error}. Tool-level negative responses (entity not found etc.) count as success.
- **`memory_mcp_tool_call_duration_seconds{tool}`** — histogram, buckets `0.01..30s`.
- **`memory_mcp_embed_calls_total{status}`** — embedding requests, status ∈ {success, empty, error}.
- **`memory_mcp_embed_call_duration_seconds`** — embed latency histogram.

A `ServiceMonitor` in `mcp-system` scrapes `/metrics` every 30s. This is currently the only instrumented MCP backend in the cluster — see [`project_todo_mcp_layer_observability_gap`](https://github.com/rwlove/home-ops) for fleet-wide rollout context.

Probes:

- **`/healthz`** — process up + Postgres `SELECT 1`. Used by liveness + startup probes.
- **`/readyz`** — `/healthz` plus Ollama `/api/tags` reachable. Used by readiness probe; returns 503 if Ollama is degraded, dropping the pod from the Service rotation until embeddings work again.

## Schema migrations

The server **does not** apply schema. A one-shot `Job` (`memory-mcp-schema-init`) under the same Kustomization runs `psql -f /sql/schema.sql` on every Flux reconcile. All DDL is idempotent (`CREATE IF NOT EXISTS`), so re-runs are no-ops.

**Job gotcha:** Kubernetes `Job.spec.template` is immutable. Editing the `command` field (e.g. the diagnostic echo) without bumping the schema-init annotation or deleting the existing Job will deadlock Flux with `field is immutable`. Recovery: `kubectl delete job memory-mcp-schema-init -n mcp-system` and let Flux recreate.

## Troubleshooting

| Symptom | First check |
|---------|-------------|
| Semantic search returns nothing | `kubectl exec memory-mcp-... -- curl http://ollama.ai.svc:11434/api/embed -d '{"model":"nomic-embed-text","input":"x"}'` — 404 = model not pulled |
| Pod `CrashLoopBackOff` on startup | Pod logs for `kg.<table> missing` — schema-init Job hasn't completed yet (or failed) |
| Flux Kustomization stuck on `field is immutable` | Job-template change without delete; see Schema migrations above |
| Embed counter `error` spiking | Ollama unreachable or model unloaded; `/readyz` will already be 503 |
| Search returns 0 for a query that obviously matches | Run with `mode=keyword` to isolate. If keyword finds it but hybrid doesn't, semantic is broken (Ollama). |

## See also

- [Offsite Recovery (Postgres restore)](./offsite_recovery.md) — for `postgres-langgraph-memory` backup recovery (Barman → Garage).
- The [project_memory_mcp_phase0_done memory entry](https://github.com/rwlove/home-ops) records the rollout history (Phases 0-3) including v0.1.x bumps + Phase 1 seed.
