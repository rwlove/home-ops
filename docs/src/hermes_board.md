# Hermes Board — Operations

Hermes is the in-cluster delegating agent: a Kanban board + dispatcher + a
fleet of worker profiles, all running the **local** vLLM model on the Spark
(zero Anthropic quota). An interactive Hermes on the bare-metal host `beast` is
the SSH-in front door that delegates into the cluster over the peer API. This
page is the operator runbook — how to reach it, how work flows, how to pause it,
and how to recover it.

| | |
|---|---|
| **App** | `kubernetes/apps/ai/hermes/` (StatefulSet `hermes-0`, ns `ai`) |
| **Board UI** | `https://board.${SECRET_DOMAIN}` — Authelia (admin tier) **+** dashboard basic-auth (user `rob`) |
| **Peer / API server** | `https://hermes-api.${SECRET_DOMAIN}` (bearer `API_SERVER_KEY`) and in-cluster `hermes-api.ai.svc:8642` |
| **Board DB** | SQLite at `/opt/data/kanban.db` on the `hermes-data` PVC (Longhorn `default` group — daily backup) |
| **Model** | vLLM `qwen3.6-35b-a3b` at `vllm-driver-spark.ai.svc:8000/v1` — all profiles, local only |
| **Health export** | OTLP/HTTP spans → `opentelemetry-collector.observability.svc:4318` → Tempo (content-free) |
| **Pod-down alerts** | `HermesPodDown` / `HermesDown` in `prometheusrule.yaml` |
| **beast front door** | `ssh hermes-beast` → `hermes chat` TUI (sshd `ForceCommand`); provisioning runbook in the plan archive |

## Delegation flow

1. From beast (or any peer): `peer run in-cluster/orchestrator "<task>"` over the
   bearer `/v1/runs` path. The orchestrator turn carries the `kanban` toolset
   (both `platform_toolsets.api_server: [hermes-cli, kanban]` **and** top-level
   `toolsets: [kanban]` — both are required, verified) so `kanban_create`
   actually writes a card instead of just narrating one.
2. The **dispatcher** (in-gateway, `kanban.dispatch_in_gateway: true`) picks up a
   `ready` card and spawns the assigned worker profile — its **own** local-35B
   profile with the full `cli` toolset, not the orchestrator's.
3. The worker runs to `kanban_complete`, or raises a `kanban_block` /
   `needs_input` when it needs a human. `max_in_progress: 2` caps concurrency.

The board card `t_2358910c` (beast → in-cluster delegation) was the end-to-end
proof this path works.

## Human-in-the-loop — blocked & needs-input cards

Board *state* lives in `kanban.db` as column values (`blocked`, `needs_input`,
`spawn_failed`), **not** in the log stream — so this surfaces in the **dashboard**,
not in an alert. Watch the board at `board.${SECRET_DOMAIN}`; a `blocked` card is
the destructive-op gate (HOMELAB-SPEC L2 #3) waiting on Rob. Clear it in the UI
(move it back to `ready`, or answer the `needs_input` prompt) and the dispatcher
re-spawns the worker.

## Kill-switch — pause dispatch

Two reliable stops, fastest first:

- **Emergency (immediate):** `kubectl scale statefulset hermes -n ai --replicas=0`.
  Stops the dispatcher and every worker at once. Restore with `--replicas=1`.
  In-flight cards return to `ready` on restart (see Recovery).
- **Controlled (GitOps):** set `kanban.dispatch_in_gateway: false` in
  `resources/config.yaml` and merge. The gateway keeps serving the board (reads
  and manual moves) but stops auto-spawning workers. This is the durable
  "board on, robots off" state.

Do **not** rely on an unverified `HERMES_KANBAN_DISPATCH_IN_GATEWAY` env
override — the two methods above are the supported kill-switches.

## Recovery

- **Stranded card after a crash:** if the gateway pod dies mid-dispatch, a card
  left `in_progress` is reclaimed to `ready` once
  `kanban.dispatch_stale_timeout_seconds` (1800 = 30 min) elapses — it does
  **not** hang for the ~4h PID-reuse window. Nothing to do but wait, or move it
  manually.
- **Board DB:** `kanban.db` is on `hermes-data` (Longhorn `default` group,
  daily/weekly/monthly backup). SQLite in WAL mode is crash-recoverable from a
  volume snapshot; there is no separate `.backup` CronJob (RWO volume can't be
  mounted by a second pod, and the volume backup already covers it).
- **Config drift:** `config.yaml`, the shell-hook allowlist, and every profile's
  `config.yaml` are re-seeded from the ConfigMap (`cp -f`) on **every** pod
  start, so an agent edit can't persistently disable its own gate or switch to a
  remote model. Personal identity (`SOUL.md`) is seed-once (`cp -n`).

## Observability

- **Gateway health export (OTel):** redacted operational diagnostics — health
  metrics, diagnostic events, and warning/error events — export as OTLP/HTTP
  spans to the collector, then Tempo. Content-free by construction: no prompts,
  messages, tool args/results, session history, or trajectories leave the pod
  (HOMELAB-SPEC L5). Find them in Grafana → Explore → Tempo, service
  `hermes-gateway`. This is the gateway's error/health signal path.
- **Liveness / reachability:** `HermesPodDown` and `HermesDown` alert on the pod
  and the blackbox `/health` probe on `:8642`.
- **Board depth:** no Prometheus exporter — the board API is cookie-auth and the
  board is small (`max_in_progress: 2`). Read depth off the dashboard / Glance
  widget directly.

## Security model (what keeps a worker boxed in)

- **Local-only inference.** Every profile pins the local vLLM `base_url`. No
  profile uses a remote provider, so nothing an agent produces is sent to a
  frontier model except through the gated escalation path below.
- **No Anthropic credential in agent env.** Workers/orchestrator get no
  `ANTHROPIC_*`; the `CLAUDE_CODE_OAUTH_TOKEN` is readable only by the
  uid-isolated `claude -p` subprocess the `terminal` tool spawns. A mis-set
  remote provider would fail auth, not leak.
- **The only remote path is gated `claude -p`.** The fail-closed
  `pre_tool_call` gate (`/opt/hooks/gate.py`, mounted read-only outside the
  writable PVC) fires on the `terminal` tool on all turns — including peer and
  worker turns — and blocks credential refs and restricted-tier paths on the
  `claude -p` branch.
- **LightRAG auto-RAG** (`pre_llm_call` hook) runs local-turns-only and wraps
  injected KG context in a `LIGHTRAG-KG` sentinel the gate blocks from reaching
  `claude -p`. Rob's data-class waiver is for the **local** agent only.
- **Vault** is the materialised `claude` Obsidian DB at `/vault` (livesync
  sidecar); agents read/write markdown there. CouchDB stays the source of truth.

## Known-benign log noise

These recur in the gateway log and are **not** faults:

- `agent.auxiliary_client: ... nous requested but Nous Portal not configured` /
  `OpenRouter fallback ... not a :free SKU — skipping` — auxiliary auto-chain
  consumers default to `provider: auto` and probe Nous/OpenRouter before falling
  back to the local model. The three named consumers (`compression`,
  `kanban_decomposer`, `profile_describer`) are pinned local; the rest degrade
  gracefully to local. Cosmetic.
- `gateway.run: Skipping secondary profile 'orchestrator' due to port-binding
  config error` — under `multiplex_profiles`, the default profile owns the single
  shared HTTP listener. Delegation runs on the default-agent path (global config
  carries the kanban toolset), so the skipped secondary profile is expected.
- A `web_tools` / `firecrawl` availability `Traceback` at startup — the `web`
  toolset is disabled; the availability probe is caught and returns unavailable.
