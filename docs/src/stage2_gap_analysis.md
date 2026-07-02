# Stage 2 — Capability Gap Analysis

Paired with `goal.md` Stage 2. This is the **deliverable before
building** — per goal: *"Show me the gap analysis before you start
closing it."*

Three sections per the spec:

1. What the current **Claude Code workflow** provides (the
   thing being replaced as Rob's primary interface)
2. What **HomeAIOps** provides today (the thing replacing it)
3. The **gap, prioritized** — what needs to be built so HomeAIOps
   becomes Rob's default

Plus a sub-audit per goal: which existing inputs feed the pipeline
today, what schema they use, and where the new CLI plugs in.

## 1. What Claude Code provides today

The current daily-driver Rob has been using to operate the cluster
(this session is itself an example). Components surfaced through
this workflow:

| Capability | Provided by | Notes |
|---|---|---|
| Interactive prompt loop | Claude Code CLI | Synchronous turn-taking; tool calls visible inline |
| Tool dispatch | `Bash`, `Edit`, `Read`, `Write`, `Agent`, MCP tools | Tools are claimed by name per turn; results stream back |
| Cluster operations | MCP tools (`kubectl_*`, `ha_*`, `omada_*`, etc.) via `lovenet-gateway` aggregator | 16 MCP servers reachable from the chat; same surface every session |
| Specialist delegation | `Agent` tool + named sub-agents (`storage-operator`, `network-operator`, etc.) | Sub-agent gets prompt + isolated context; returns one report |
| Conversation history (per session) | Implicit — `claude` CLI's transcript file | Session = ~/.claude/projects/...; replayable; survives `/clear` only by explicit save |
| Cross-session memory | Auto-memory at `~/.claude-personal/projects/<cwd-encoded>/memory/` | Per-CWD partitioned; index in `MEMORY.md`; manual write via Rob's instruction |
| Todo / task tracking | `TaskCreate` / `TaskUpdate` / `TaskList` tools | Lives **only in current session's transcript**; not durable across sessions, not visible to other surfaces |
| Background work | `Bash run_in_background=true`, `Monitor`, `ScheduleWakeup` | Notifications come back as system reminders |
| Scheduled / recurring | `/loop`, `/schedule`, `CronCreate` (deferred) | Time-based dispatch through the harness, but Rob still has to be present-ish |
| Output formats | Markdown directly to chat | No persistent artifact unless Rob asks for a file or commit |
| Model selection | `/model` slash-command; built-in routing | Opus 4.7 default in this session; alternatives are CLI-flagged |
| Escalation | Manual — Rob can re-prompt or ask for a different sub-agent | No automatic "this is hard, get a bigger model" path |
| Cost visibility | None at the CLI layer; Anthropic billing dashboard is out-of-band | Per-session token use only visible if Rob queries the SDK |
| Authentication / authorization | Local Claude Code config + Anthropic API key in env | Single user (Rob) |
| Surfaces (input) | Terminal only | One human, one keyboard |

Net characterization: Claude Code is a **single-user synchronous
agent loop** with strong tool integration and weak durability.
Brilliant for one-at-a-time work; thin for queued work, scheduled
work, or work that needs to outlive a terminal session.

## 2. What HomeAIOps provides today

Sourced from `ai_architecture.md` and verified through the Stage 1
audit. Pipeline shape is:

```text
surface → bridge → langgraph-agents queue → triager → specialist → output
                                              ↓                        ↑
                                          (Claude API escalation)      (vault / Zulip / ntfy / Langfuse)
```

| Capability | Provided by | Notes |
|---|---|---|
| Multi-surface input | HA voice, Zulip DM (Triager), Open WebUI, Khoj, AlertManager, Cron, ntfy tap | Voice + chat + push + scheduled all already wired |
| Durable task queue | `task_queue` + `task_dlq` tables in `postgres-langgraph-checkpoints` | Backed by Postgres LISTEN/NOTIFY; 3-replica CNPG |
| Task dispatch | `langgraph-agents` queue worker | Claims tasks, calls graph, writes results back |
| Specialist routing | Triager agent (LLM-driven) | Decides which specialist gets the task based on content |
| Specialist execution | 13 named agents (supervisor, researcher, coder, reviewer, triager, reporter, note-maker, homelab-engineer, smart-home-operator, ml-operator, errand-runner, property-coordinator, health-tracker) | All run inside langgraph-agents pod; share the same graph |
| Approval gate | `interrupt()` + Windmill `langgraph-approval-post.ts` + ntfy/Zulip | Class A/B/C/D taxonomy; ntfy tap → /approval endpoint |
| Model selection | Per-agent default (qwen3-next:80b-a3b-instruct-q4_K_M on Spark) + `requires_cloud` tag → Claude API escalation | Cost caps enforced in-cluster: `$5/task`, `$10/agent/day`, `$30/global/day` |
| Inference backends | ollama-spark (GB10), ollama (P40), tei-spark (reranker), Claude API | Routing in `langgraph-agents/.agents/instructions/hardware-routing.md` |
| Tool surface | MCP Gateway + 16 MCP servers via Istio | Same set as Claude Code, accessed differently (HTTP MCP not CLI MCP) |
| Persistent outputs | Vault (`langgraph-vault` PVC + RO sync from laptop), Zulip threads, ntfy push, Langfuse traces | Outputs land in multiple sinks per task |
| Cross-agent memory | `memory-mcp` (Postgres + pgvector KG) | bge-m3 embeddings via ollama-spark |
| Cost / spend visibility | `/admin/costs/today` endpoint + Windmill `langgraph-cost-cap-watcher.ts` | Aggregates per-task / per-agent / global; Pushover alert on cap |
| Observability | Langfuse OTLP traces, Prometheus metrics, Loki logs | Per-task trace ID; spans for triager/specialist hops |
| Trace correlation | Each task has a `trace_id` propagated through langgraph + Windmill + Langfuse | Cross-surface continuity |
| Surfaces (output) | Vault file, Zulip DM reply, ntfy push, Langfuse trace | Default depends on agent + source |
| Authentication / authorization | Per-surface (Authelia for Open WebUI/Khoj, Zulip bot tokens, ntfy app tokens, internal cluster network for Windmill→langgraph) | Already wired; Rob is the single user |

Net characterization: HomeAIOps is a **multi-surface durable task
pipeline** with strong async/queue handling and weak interactive
ergonomics. Excellent for "fire and forget" work; thin for the
real-time turn-taking style Claude Code excels at.

## 3. Existing input-contract audit

Per the goal: *"confirm: which existing inputs feed the pipeline
today, what schema/contract they use, and where the CLI plugs into
that same contract."*

The langgraph-agents `POST /inbox` endpoint is the **single
choke-point**. Every bridge POSTs the same shape:

```typescript
type InboxBody = {
    task_id: string;     // REQUIRED — server rejects without it
    source: "voice" | "zulip" | "text" | "holmesgpt" | "test" | "openwebui";
    content: string;     // REQUIRED — the user's intent
    user?: string;       // defaults to "rob"
    zulip_user_id?: number;  // optional; if present, reply-back goes via triager-bot DM
};
```

Existing producers:

| Producer | Source value | task_id format | Reply-back path |
|---|---|---|---|
| `langgraph-inbox.ts` (Windmill) | passthrough from caller (default "voice") | passthrough or generated | varies by caller |
| `zulip-triager-webhook.ts` | `"zulip"` | `zulip-<ts>-<rand>` | triager-bot DM via `zulip_user_id` |
| Open WebUI (agent-as-model) | `"openwebui"` | OpenAI-compat chat completion id | inline OpenAI-style response |
| HolmesGPT | n/a — separate path | n/a | n/a — HolmesGPT does not route through `/inbox` today |
| AlertManager → HolmesGPT | n/a — Windmill workflow calls `holmesgpt/investigate`, not `/inbox` | n/a | Pushover + Zulip via separate workflow |

Important: HolmesGPT and AlertManager **do not** share the
`/inbox` contract. They have their own pipeline. That's by
design (alerts → diagnostic agent → no specialist routing
needed).

The **contract is consistent across the surfaces that DO use
`/inbox`**. Good — no implicit-contract refactor needed. The
CLI plugs in as a 6th producer:

```typescript
// what the CLI will POST:
{
    task_id: `cli-<ts>-<rand>`,
    source: "cli",                  // NEW value — needs langgraph-agents enum update
    content: <whatever Rob typed>,
    user: "rob"
    // no zulip_user_id; reply-back is the CLI's own polling loop
}
```

One small server-side change: langgraph-agents' Pydantic source
enum needs `"cli"` added. One-line PR in the langgraph-agents
repo; pairs with whatever-image-tag-bump in home-ops.

## 4. The gap, prioritized

What Claude Code has that HomeAIOps lacks — ranked by how much
each blocks Rob's actual workflow when he switches to CLI-first.

### Tier 0 — blockers; CLI cannot ship without these

1. **No CLI surface exists.** A terminal command that POSTs to
   `/inbox` and shows the result. This is the headline Stage 2
   deliverable.

2. **No `"cli"` value in langgraph-agents' source enum.** One-
   line langgraph-agents change. Trivial; pairs with the image
   bump renovate auto-generates.

3. **No result-streaming or fetch-by-task-id endpoint usable from
   CLI.** Current state:
   - `POST /inbox` returns `{task_id, status, output?, paused_for?}` synchronously, but `output` is null until the task completes async.
   - The CLI needs either (a) long-poll the queue for a specific task_id, (b) subscribe to a server-sent-events / websocket stream, or (c) wait for an inline output if the task is fast.
   - Decision needed (see Open Questions).

### Tier 1 — needed within Stage 2 DoD

1. **Todo management.** Goal explicitly: *"Move todo management
   out of Claude Code into HomeAIOps' own store. Claude Code
   becomes a consumer, not the store."* HomeAIOps has NO durable
   todo surface today. Claude Code's `TaskCreate`/`TaskUpdate`
   tools are session-local. **Build needed**: a todo store in
   the pipeline (probably a new postgres table colocated with
   `task_queue`), CRUD endpoints, and a CLI subcommand
   (`hai todo add` / `hai todo ls` / `hai todo done`). Claude
   Code consumes via either a new MCP server or by shelling out
   to `hai`.

2. **Conversation continuity / thread context.** Claude Code
   sessions accumulate context turn by turn; each HomeAIOps task
   today is independent. For "ask follow-up about the task I
   just asked about," the CLI needs a thread / conversation_id
   primitive. **Build needed**: optional `conversation_id` on
   `/inbox` body; agents pull prior turns from checkpoint store
   on resume.

3. **Cross-session memory parity.** Claude Code's
   `~/.claude-personal/projects/.../memory/` is per-CWD and
   human-readable. HomeAIOps has `memory-mcp` (Postgres
   pgvector). For Rob's CLI workflow to feel continuous,
   either:
   - **Bridge** — `memory-mcp` exposes a Claude-Code-style
     interface (read/write memory entries) the CLI can use, or
   - **Sync** — a periodic job reflects vault `/memory/*.md`
     into `memory-mcp` and back.

### Tier 2 — quality-of-life; nice for dogfooding to work

1. **Per-turn tool-call visibility.** Claude Code shows each
   tool call inline. HomeAIOps logs go to Langfuse asynchronously.
   For CLI dogfooding to not feel like a regression, the CLI
   should either (a) tail Langfuse spans for the task in
   near-real-time, or (b) langgraph-agents emits structured
   per-step events the CLI can subscribe to.

2. **Cost / spend visibility surfaced at the CLI.** Goal calls
   for "Cost/usage visibility — CLI subcommand showing local
   vs. escalated task counts and Claude spend over the last N
   days." The data exists at `/admin/costs/today`; needs a
   `hai cost` subcommand that hits it.

3. **Routing policy file in repo.** Stage 3 piece but flagged
   in Stage 2 because the gap analysis surfaces it: today the
   "local vs. escalate" decision lives **in code** in
   langgraph-agents (`requires_cloud` tag + uncertainty
   threshold). Goal wants this as a **version-controlled file
   that changes behavior when edited**. Defer to Stage 3
   unless trivial.

4. **Per-session naming / threading in HomeAIOps.** Tasks
   today are anonymous to the user. Adding a friendly
   "session" / "thread" handle (`#stage2-cli-work`) makes
   multi-task workflows browseable.

### Tier 3 — out of Stage 2 scope; flag for Stage 3+

1. **Routing policy as version-controlled file** (Tier 2 #9
   fully fleshed out).
2. **Escalation client uniformity** — Claude Code (headless)
   vs. Claude API choice spelled out as a policy.
3. **Provenance / per-task input-source recording** — already
   in trace_id but not surfaced.
4. **Local-vs-escalate budget guardrail with auto-warning.**

These are explicitly Stage 3 per goal.md and stay there.

## 5. Where the CLI plugs in

Concrete shape (subject to your review):

```text
~/bin/hai task add "<intent>"          → POST /inbox source=cli content=<intent>; print task_id; tail by default
~/bin/hai task tail <task_id>          → poll/stream until done; print outputs
~/bin/hai task ls                      → GET /admin/tasks (today's); pretty-print
~/bin/hai task show <task_id>          → GET /admin/tasks/<id> + Langfuse trace deep-link
~/bin/hai todo add "<note>"            → new endpoint; persists to new todo table
~/bin/hai todo ls                      → list pending todos
~/bin/hai todo done <id>               → mark complete
~/bin/hai cost                         → GET /admin/costs/today; pretty-print + recent history
~/bin/hai chat <task_id> "<follow-up>" → re-POST /inbox with conversation_id; show streamed output
```

Implementation language: probably Python (matches
langgraph-agents) or Go (single static binary, no PYTHONPATH
issues). Defer to your call.

Installation: `$PATH` per the goal's "installed on $PATH,
`--help` works" requirement.

Distribution: live in the `langgraph-agents` repo as a
subcommand (mirrors how the queue worker + agents are also
there), with `pyproject.toml` exposing `hai` as a console script
that pip-installs to `~/.local/bin`.

Auth: cluster-internal access for the daemon (it talks to
`langgraph-agents.ai.svc.cluster.local`). For Rob's laptop:
either a `cloudflared` tunnel to the langgraph-agents service
(behind Authelia like Open WebUI is) OR `kubectl port-forward`
in the CLI's preamble. Either works; the former is more
elegant.

## 6. Open questions / decisions needed before I build

Per goal.md *"When in doubt about scope, intent, or whether
something counts as 'done' — ask."*

### Q1. Result delivery model

For `hai task add` to feel responsive at the terminal, how does
the CLI get the result?

- **(a) Sync block** — `/inbox` already returns immediately with a `status: "accepted"`. CLI then polls a new endpoint like `GET /admin/tasks/<id>/result` every 1-2s until status flips to `done`. Simple; works today with a small server-side addition (`/admin/tasks/<id>/result` returning the queue row's `result` column). What's printed to terminal is the final output blob.
- **(b) Server-sent events** — langgraph-agents adds `GET /admin/tasks/<id>/stream` returning SSE: per-node events (`node_start triager`, `node_end note-maker → output preview`, etc.). CLI prints each event as it lands. Closer to the "Claude Code shows tool calls inline" experience but more server work.
- **(c) WebSocket** — same as (b) but websocket. Probably overkill.

My recommendation: **(a) for v1**, **(b) once we know v1 works**. Locks the CLI design but doesn't bet the farm on the streaming infrastructure.

### Q2. Todo store shape

Two ways to add durable todos:

- **(a) Separate `todo` table** in `postgres-langgraph-checkpoints` schema. Simple CRUD. Independent lifecycle from tasks.
- **(b) "Todo" as a special task type** — todos are queued tasks that never get dequeued; they live in `task_queue` with a "type" tag and never reach a specialist. Closer to the cluster's existing primitives but conflates "intent to do later" with "intent to dispatch now."

My recommendation: **(a) separate `todo` table**. Cleaner semantics; doesn't pollute task queue counts; lets todos have their own constraints (no TTL, no idempotency, no agent assignment).

### Q3. Auth path for laptop CLI

- **(a) Cloudflared tunnel + Authelia + new HTTPRoute** for `/admin/*` (today only `/approval` is exposed). Symmetric with Open WebUI / Khoj / etc.
- **(b) `kubectl port-forward` in the CLI's preamble**. No new public surface; simpler; assumes Rob's laptop has `kubectl` configured (which it does).

My recommendation: **(b) for v1**. Avoids opening a new public ingress while we're still iterating on the API contract. Promote to (a) when the API is stable.

### Q4. Where does the CLI source code live

Per the audit, `langgraph-agents` already hosts the queue worker
plus agents and is where the `source` enum needs updating.

- **(a) Live in `langgraph-agents` repo** — single source of truth for the contract + the client; pyproject.toml exposes `hai` console script.
- **(b) New `hai-cli` repo** — separation of concerns; client can release independently of agent fleet.

My recommendation: **(a) live in `langgraph-agents`**. The CLI and the API are coupled-by-design (CLI is a thin client over `/inbox` + `/admin/*`); separate repos add coordination overhead without separating actual concerns.

### Q5. What's the bar for "dogfooded for a day"?

Per goal.md Stage 2 DoD: *"One full day of dogfooding without
falling back to Claude Code for the primary task flow."*

Two interpretations:

- **(a) Strict** — any CLI session that worked through Claude Code for one full day. Hard to verify without instrumentation.
- **(b) Pragmatic** — Rob keeps a "what I did today" log; if he can credibly say "I used `hai` for all the things I'd have asked Claude Code to do" at end of day, that's a pass.

My recommendation: **(b)**. Stage 3 has stricter measurement
(escalation-rate dashboards); Stage 2 dogfooding is the
acceptance test, not the metrics.

## 7. Stage 2 build plan (proposed sequence, post-review)

Pending your answers to Q1-Q5, my proposed order:

1. **Update langgraph-agents `source` enum** to include `"cli"`. One-line PR.
2. **Add `/admin/tasks/<id>/result` endpoint** to langgraph-agents (assumes Q1 = (a)). Small PR.
3. **Add `todo` table + CRUD endpoints** to langgraph-agents (assumes Q2 = (a)). Migration + 4 endpoints.
4. **Write the `hai` CLI**, living in `langgraph-agents/cli/` (assumes Q4 = (a)). Subcommands: `task add/tail/ls/show`, `todo add/ls/done`, `cost`.
5. **Image bump + helmrelease deploy** through renovate.
6. **One day of dogfooding** — Rob runs everyday work through `hai`; I keep notes on rough edges.
7. **Gate 2 evidence PR** in home-ops with day-of-dogfooding log per goal.

Each step is one PR. Tier 2 items (#7, #8 from gap section) get
added between #5 and #6 if dogfooding surfaces a need.

## 8. What I am NOT proposing in Stage 2

Per goal.md scope discipline:

- **Routing policy file** — Stage 3. Even though it's tempting because we're touching the routing layer in #1.
- **Auto-escalation logic changes** — Stage 3.
- **Cost-cap watcher rewrite** — works today; leave it.
- **Per-step event streaming** (Q1 option (b)) — defer until v1 sync polling proves the design.
- **MCP server for memory parity** — Tier 1 item #6 is real but I'd rather get the CLI shipped first and figure out memory in a v2 cycle. Flag for next gate.

## 9. Operator decisions (2026-05-21)

Rob answered Q1-Q5:

1. **Q1 = (a) sync poll.** CLI polls `GET /admin/tasks/<id>/result` until terminal status. Defer SSE to v2.
2. **Q2 = (a) separate `todo` table.** Distinct lifecycle from `task_queue`.
3. **Q3 = (a) cloudflared / Authelia.** *(2026-07-01: shipped as gateway extAuth SecurityPolicies on the `hai`/`hai-web` routes rather than an oauth2-proxy instance — the fleet was retired in #12767; `/admin/*` + `/inbox` ride a policy-less public route with app-level auth.)* Public surface from day one — `hai.${SECRET_DOMAIN}` behind Authelia + oauth2-proxy, same pattern as Open WebUI / Khoj / etc. This changes the build plan: a new HTTPRoute for `/admin/*`, new oauth2-proxy instance (or reuse Open WebUI's), DNS via external-dns. The CLI uses a long-lived API token stored at `~/.config/hai/token`; minting flow TBD (likely a one-time `hai auth login` device flow, or operator-issued static token to start).
4. **Q4 = (a) live in `langgraph-agents` repo.** CLI ships as a console_script in `pyproject.toml`.
5. **Q5 = (b) pragmatic dogfood.** Acceptance is Rob's credible end-of-day "I used `hai` for everything I'd have asked Claude Code." No instrumented metric.

### Build sequence (final, post-decisions)

1. **langgraph-agents PR-A** — `"cli"` source enum + `GET /admin/tasks/<id>/result` + `todo` table migration + CRUD endpoints + Pydantic models. One PR, one migration.
2. **home-ops PR-B** — new HTTPRoute `hai.${SECRET_DOMAIN}` → `langgraph-agents:8765`, oauth2-proxy in front (reuse `langgraph-agents-oauth2-proxy` pattern from existing apps), Authelia client config, external-dns A record, CNP egress. Self-contained infrastructure.
3. **langgraph-agents PR-C** — the `hai` CLI: `cli/` subdir, console_script entry point in pyproject.toml, `hai task add/tail/ls/show`, `hai todo add/ls/done`, `hai cost`, `hai chat`, basic auth via token-file. Sync poll loop for results.
4. **Image release** — tag `v0.2.35` after PR-A merges; later `v0.2.36` after PR-C merges. (Or batch into one release.)
5. **home-ops PR-D** — image bump for the release(s) via renovate auto.
6. **Dogfood day** — Rob installs `pip install langgraph-agents` (or uses `pipx`) on his laptop, runs `hai auth login`, replaces normal Claude Code usage with `hai`.
7. **home-ops PR-E (Gate 2)** — evidence: dogfood log, transcript / sample tasks, confirmation that other non-CLI inputs still work (Zulip DM, voice, etc.), then your Gate 2 approval.

### Open question for during build

**Auth token issuance.** Three flavors:

1. *Static operator-issued token* — simplest. Generate once, put in 1Password under a new `hai-cli` item, ExternalSecret materializes it in the cluster, CLI reads `~/.config/hai/token` populated by `hai auth set-token <value>`. v1 ships with this.
2. *Device flow* — `hai auth login` opens a URL, Rob authorizes in browser, CLI receives refresh token. More user-friendly but more code; defer to v2.
3. *Long-lived OAuth client credentials* — overkill for a single-user CLI.

I'll go with **(1)** for v1 unless you say otherwise. The token can be replaced with device flow in a later iteration.

## 10. Status as of 2026-05-23 (post Stage-1 Gate signoff)

Build sequence steps 1-5 from §9 are complete. Verification:

| Step | Status | Evidence |
|---|---|---|
| #1 lga PR-A — `"cli"` source enum + `/admin/tasks/<id>` + todo table | ✅ | `src/agents/state.py::Source` includes `"cli"`; `src/agents/api/todos.py` present; lga PRs #66-#68 merged |
| #2 home-ops PR-B — `hai.${SECRET_DOMAIN}` route + auth + DNS | ✅ | `hai.${SECRET_DOMAIN}` resolves to the cluster gateway; CLI auth works against it |
| #3 lga PR-C — `hai` CLI in `cli/` subdir with subcommands | ✅ | `which hai` → `/home/rwlove/.local/bin/hai`; `task add/tail/ls/show`, `todo add/ls/done`, `cost`, `auth` all present |
| #4 Image release | ✅ | tags v0.2.34 through v0.2.42 published |
| #5 home-ops image bump (Renovate auto) | ✅ | cluster pod on v0.2.42 as of 2026-05-23 17:08Z |

Plus Stage-1.5 UX work landed 2026-05-23 (additive to the original
plan — closing gaps that emerged from real Stage-1 dogfooding):

- Reporter agent → universal final hop renders raw specialist output
  as rich-text Zulip DMs with clickable `obsidian://` vault links +
  labeled URLs (lga#73/#75/#76/#77 + home-ops#11997).
- Agent definitions migrated from vault into the lga repo — persona
  changes ship as PRs with image-tag traceability (lga#71/#73/#75).
- `ADMIN_NAME` interpolated at the DM render boundary only —
  repo files stay generic per [[user_class_architecture]]
  (home-ops#11990).
- `aihomeops-state` Grafana dashboard — single-pane view of queue
  depth, task state, escalation gates, cost (home-ops#11989).
- Smart-home `device-intent-map.yaml` + drift-detect Windmill
  workflow (lga#78 + home-ops#11998).

### What remains for Stage 2 DoD

**Step 6: Dogfood day.** Wall-clock day where ADMIN uses `hai` for
the work that previously would have been Claude Code sessions.
Per §9 Q5 decision: acceptance is ADMIN's credible end-of-day
"I used `hai` for everything I'd have asked Claude Code."

**Step 7: Gate 2 evidence PR.** Post-dogfood:

- Log of the day's `hai` usage
- Any fall-back-to-Claude-Code events (each is a P0/P1 gap the
  analysis missed)
- Confirmation that other non-CLI inputs still work (Zulip DM,
  Renovate-triage, AlertManager→HolmesGPT, daily-digest)

### Gaps still recommended to close before the dogfood day

Re-reading §4 against current state, two items from earlier tiers
remain actionable:

- **Tier 1 #2 (conversation continuity / `conversation_id`):** still
  not built. For multi-turn workflows where ADMIN wants
  follow-ups on the same context, today each `hai task add` is
  independent. Decide whether this is a v1 gap or punt to v2 based
  on what the dogfood day surfaces.
- **Tier 2 #6 (cost local-vs-escalated breakdown):** `hai cost`
  exists; verify it shows local-vs-escalated split (data is in
  Langfuse + `accumulated_cost_usd`).

No new gaps surfaced from the Stage-1.5 work that aren't already in
§4. The reporter agent / DM rendering / dashboard / intent map are
all complementary to (not duplicative of) the CLI work.

## End

Build starts after this PR merges.
