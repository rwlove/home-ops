# Khoj — personal RAG surface

[Khoj](https://khoj.dev) is the cluster's personal-document RAG surface — separate from the [langgraph-agents fleet](ai_architecture.md). Different problem, different deployment, different API.

| Aspect | langgraph-agents | Khoj |
|---|---|---|
| Shape | task-in / side-effect-out | conversational chat |
| State | task queue + checkpointer | persistent chat threads |
| Specialists | 18-agent fleet (triager → specialist → reporter) | per-user custom agents |
| Use case | "do this thing" / "triage these PRs" | "what did I write about X" / "summarize this URL" |
| Surface | `hai` CLI, Zulip bot, Open WebUI | `khoj.${SECRET_DOMAIN}` web UI |
| Side effects | yes (via errand-runner) | no (read-only) |

## Where it lives

`kubernetes/apps/ai/khoj/`:

- **Pod**: `ghcr.io/khoj-ai/khoj:2.0.0-beta.28`, port 42110
- **External hostname**: `khoj.${SECRET_DOMAIN}` behind Authelia (gateway extAuth `SecurityPolicy`, admin tier; `/api/*` bypasses gateway auth for khoj API tokens)
- **Internal mode**: `--anonymous-mode` (every request is the auto-bootstrapped "default" user — safe because oauth2 is the gate)
- **Database**: `postgres-khoj` (CNPG 3-replica, pgvector embeddings)
- **PVCs**: `khoj-config` (`/root/.khoj/`), `khoj-models` (`/root/.cache/` for sentence-transformer downloads)
- **LLM**: Ollama via OpenAI-compat API (`http://ollama.ai.svc.cluster.local:11434/v1`), default model `qwen2.5:7b` (P40)
- **Web search**: searxng (`http://searxng.collab.svc.cluster.local:8080`)
- **Gated off** (intentional): `KHOJ_OPERATOR_ENABLED` (browser automation), `KHOJ_TERRARIUM_URL` (sandboxed Python)

## Agents

Two agents are configured today:

| Slug | Tools | Use it for |
|---|---|---|
| `khoj` | none (general only) | Default — admin-managed, can't be modified via the API |
| `homelab-*` | `general` + `online` + `notes` + `webpage` | Search ADMIN's notes for cluster context; fall back to web with citations |

Switch agents from the UI's left rail. Default selection is `Khoj` (no tools); pick `Homelab` for cluster/HA/storage questions.

## What's working today

- **Web search via searxng** — `online` tool on the `Homelab` agent. Ask anything; khoj searches + summarizes + cites.
- **URL ingestion** — `webpage` tool reads a URL you paste.
- **Conversational thread** — chats persist across sessions in `postgres-khoj`.

## What's NOT working until content is indexed

- **`notes` tool** — searches a local knowledge base that's currently empty (`files: []` on every agent, zero entries in pgvector). When you ask "find any notes about my homelab," the `notes` lookup returns nothing.

The cluster can't index your Obsidian vault on its own (the vault lives on your laptop, syncs via obsidian-couchdb). **You install a plugin on the laptop side; the plugin pushes vault content into khoj.**

## Connecting your Obsidian vault

This is a one-time laptop-side setup. Steps:

1. **In Obsidian**: Settings → Community Plugins → Browse → search "Khoj" → install + enable.
2. **Plugin config**:
   - `Khoj URL`: `https://khoj.${SECRET_DOMAIN}`
   - `Khoj API key`: mint one in the khoj web UI → Settings → API → Create Token. Paste it here.
3. **Pick what to sync**:
   - "Sync all" — every `.md` in the vault
   - Or restrict by folder (e.g., only `~/vaults/claude/projects/home-ops/`)
4. **Run a sync** — the plugin uploads files; khoj embeds them into pgvector. First sync takes minutes; deltas are seconds.

Once content is indexed:

- The `notes` tool on the `Homelab` agent will return matches with paragraph citations.
- Searches use semantic similarity (sentence-transformers + pgvector), not just keyword grep.
- You can ask "what did the inspection-fix plan say about the basement" and get cited results from your `~/vaults/personal/property/3532-foxhall/inspection-fix-plan.md`.

## Other content sources

Beyond Obsidian, khoj supports:

- **Khoj Desktop app** — sync arbitrary folders from your laptop
- **Web UI drag-and-drop** — one-off uploads for ad-hoc reference
- **Gmail/IMAP** — ingest email threads (requires app password + IMAP creds)

Each is opt-in laptop-side.

## Operator and Terrarium (gated off — read before enabling)

The `helmrelease.yaml` leaves these env vars unset:

- **`KHOJ_OPERATOR_ENABLED`** — enables computer-use mode (the LLM controls a sandboxed browser, clicks buttons, fills forms). Powerful for "go book me a flight"-style tasks; security implications include the agent reaching auth'd pages, clicking transactional buttons, etc.
- **`KHOJ_TERRARIUM_URL`** — points at a sandboxed Python execution service (khoj's "Terrarium" sidecar — separate deployment). Lets the `code` tool actually run Python. Without it, the `code` tool is no-op.

Neither is currently in scope for cluster deployment. If you want either, the safety story (network egress, secrets exposure, blast radius) needs review first.

## API access

For programmatic use beyond the web UI:

- `GET https://khoj.${SECRET_DOMAIN}/api/agents` — list configured agents
- `POST https://khoj.${SECRET_DOMAIN}/api/agents` — create a new agent (PATCH for updates, but only on agents you created — admin-managed ones are read-only via this API)
- `POST https://khoj.${SECRET_DOMAIN}/api/chat?q=<question>&agent=<slug>` — one-shot chat call
- WebSocket: `wss://khoj.${SECRET_DOMAIN}/api/chat/ws?client=web` — streaming chat

Auth: API token from the web UI (Settings → API).

## Troubleshooting

**Empty chat responses** — likely no tools enabled on the agent OR no content indexed. Default `Khoj` agent has no tools. Switch to `Homelab`, or enable tools in the UI's agent editor.

**`KeyError: 'image'` / `KeyError: 'docx'` in pod logs** — khoj's content-type registry doesn't recognize the file. The errors observed 2026-05-23 13:44 UTC were one-off (presumably from a manual desktop sync attempt that included unsupported file types). Filter the laptop-side sync to `.md` only.

**Conversation lost or duplicated** — check `postgres-khoj` cluster health. CNPG is the durable store; pod restarts are stateless.

**LLM responses slow** — khoj uses P40-served `qwen2.5:7b` by default. To route to a different model, change `KHOJ_DEFAULT_CHAT_MODEL` in `helmrelease.yaml` and recreate.
