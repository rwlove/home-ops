-- memory-mcp knowledge-graph schema for the langgraph_memory database.
-- Applied idempotently by schema-init-job.yaml. Re-running is a no-op.
-- See plan: ~/.claude-personal/plans/i-would-like-memory-ethereal-pebble.md
--
-- The vchord + vector extensions are already enabled at CNPG bootstrap
-- (see langgraph-memory cluster.yaml postInitApplicationSQL).

CREATE SCHEMA IF NOT EXISTS kg;

CREATE TABLE IF NOT EXISTS kg.entities (
  id          BIGSERIAL PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  type        TEXT NOT NULL,
  namespace   TEXT,
  source      JSONB NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kg.observations (
  id          BIGSERIAL PRIMARY KEY,
  entity_id   BIGINT NOT NULL REFERENCES kg.entities(id) ON DELETE CASCADE,
  content     TEXT NOT NULL,
  -- 1024-dim per Phase A of the Spark-unblocks-RAG plan (bge-m3
  -- beat nomic-embed-text by +23 MRR@10 pts on Paperless retrieval).
  -- The 2026-05-20 migration ALTERed an existing 768-dim column
  -- USING NULL + re-embedded inline; CREATE TABLE IF NOT EXISTS
  -- here only affects fresh-cluster installs.
  embedding   vector(1024),
  source      JSONB NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS kg.relations (
  id           BIGSERIAL PRIMARY KEY,
  from_entity  BIGINT NOT NULL REFERENCES kg.entities(id) ON DELETE CASCADE,
  to_entity    BIGINT NOT NULL REFERENCES kg.entities(id) ON DELETE CASCADE,
  type         TEXT NOT NULL,
  source       JSONB NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (from_entity, to_entity, type)
);

CREATE INDEX IF NOT EXISTS kg_obs_entity_idx
  ON kg.observations(entity_id)
  WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS kg_ent_type_idx ON kg.entities(type);
CREATE INDEX IF NOT EXISTS kg_ent_namespace_idx ON kg.entities(namespace);

-- vchord HNSW over observation embeddings. vchord auto-tunes `lists`
-- on startup (see project_immich_clip_index_rebuild) so we don't set it.
CREATE INDEX IF NOT EXISTS kg_obs_embedding_hnsw
  ON kg.observations
  USING vchordrq (embedding vector_cosine_ops);
