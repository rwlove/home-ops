# Hermes — the local-first agent front door

Hermes (Nous Research's [hermes-agent](https://github.com/NousResearch/hermes-agent))
runs in the `ai` namespace as the cluster's **local-first routing front
door**. It answers on a local model by default and escalates only the hard
reasoning to the Claude subscription — so the cheap and ambient work, and the
whole tool-calling execution loop, draw **zero Anthropic quota**.

This chapter is **operational** — how Hermes is wired, how to reach it, and
what to do when it misbehaves. The design rationale lives with the code and in
the vault; this is the runbook.

## Why it exists

Before Hermes, every request went to the frontier tier. Hermes is the routing
substrate that keeps the frontier model for what only it can do and sends
everything else down a cheaper path:

| Tier | Runs on | Used for |
|---|---|---|
| **Local 35B** (Spark) | `vllm-driver-spark` (Qwen3.6-35B-A3B) | ambient work + the tool-calling **execution loop** of larger tasks — zero Anthropic quota |
| **Reasoning consult** | `claude -p` on the Max subscription | only the hard planning/architecture step; the 35B executes the returned plan locally |
| **Interactive Claude** | Claude Code on the laptop | genuinely hard, exploratory, or destructive work with a human in the loop |

The saving comes from two places: ambient work done entirely local, and — on
hard tasks — the token-heavy execution loop running local while only a bounded
reasoning consult hits the subscription.

## Shape

- **StatefulSet** `hermes` (single writer — session/memory stores are not
  concurrency-safe). Image `nousresearch/hermes-agent`, run as `gateway run`.
- **OpenAI-compatible API** on `:8642`, in-cluster only, **key-gated**
  (`API_SERVER_KEY`). `/health` is unauthenticated; `/v1/*` requires the key.
  There is deliberately **no public HTTPRoute** — an unauthenticated Hermes
  API server / dashboard was the entry point for a mid-2026 agent-backdoor
  campaign, so the surface stays inside the cluster.
- **State** on a dynamic Longhorn PVC (`hermes-data`, xfs, 2 replicas, `default`
  backup group) at `/opt/data` — SQLite/FTS5 sessions, memory, self-authored
  skills, and identity. Block+xfs keeps SQLite on WAL.
- **Primary model** points at `vllm-driver-spark` with `streaming: false` (the
  driver's `qwen3_xml` + reasoning parser leaks tool calls into plain text
  under streaming — the execution loop needs real `tool_calls`).

## The security gate (non-negotiable floor)

The escalation bridge lets Hermes shell out to `claude -p`. Every `terminal`
tool call passes through a **fail-closed `pre_tool_call` gate** first:

- **`gate.py` is mounted read-only** at `/opt/hooks`, physically outside the
  writable `/opt/data` PVC — so a self-authored skill cannot rewrite its own
  gate.
- **`fail_closed: true`** — a timeout, error, or exit-2 all **block** the tool.
  A bug in the gate denies rather than opening.
- It refuses `--dangerously-skip-permissions`, blocks credential exfiltration,
  and runs the **escalation redaction**: restricted-tier content (media paths,
  `kubernetes/apps/{media,security}`, MACs, 1Password refs) is never serialized
  to `claude -p`.
- The hook is **consented declaratively** via a seeded
  `shell-hooks-allowlist.json` (Hermes skips un-consented hooks, so this is what
  makes the gate actually fire — verify with `hermes hooks doctor`).

`config.yaml` and the gate are re-applied from their ConfigMaps on every start
(`cp -f`), so an agent edit can't persistently disable the gate.

## Operating it

Run these against the `ai` namespace.

| Need | Command |
|---|---|
| Health | `kubectl -n ai get pod hermes-0`; `curl :8642/health` (via port-forward) |
| Is the gate live? | `kubectl -n ai exec hermes-0 -c app -- hermes hooks doctor` → expect `✓ allowlisted, ran clean` |
| Logs | `kubectl -n ai logs statefulset/hermes --tail=100` |
| Restart | `kubectl -n ai rollout restart statefulset/hermes` |
| Reach the API | `kubectl -n ai port-forward pod/hermes-0 8642:8642`, then hit `/v1/chat/completions` with `Authorization: Bearer <API_SERVER_KEY>` |

The Claude subscription token (`claude setup-token`, ~1 year) and the API key
live in the 1Password `hermes` item — **its own item**, not the shared
`claude-runner` one, so a claude-runner teardown can't break Hermes.

## What's watched

| Alert | Fires when |
|---|---|
| `HermesPodDown` | pod not-Ready >10m (image-pull, OOM, crashloop, bad init) |
| `HermesDown` | `/health` probe failing >5m (up but not serving) |
| `SparkVllmDriverWedged` | the local model's synthetic-decode probe fails (Hermes' execution tier is down) |
| `SparkVllmDriverToolCallBroken` | the driver stops returning well-formed `tool_calls` (a parser regression silently breaks Hermes' execution loop) |

All are `warning` — Hermes is tooling, not a household-facing service, and the
local model and escalation both keep working directly if the gateway itself is
down.

## Known limits

- **NVFP4** — the driver serves FP8 today; the ~2× NVFP4 upgrade is staged for
  a maintenance window.
- **Economics of the bridge** — routing ambient work local is a certain win;
  whether the `claude -p` reasoning consult *nets* cheaper than an interactive
  turn (its base context is heavy) is measured, not assumed.
- **Unattended lanes** — Hermes runs interactive (client-trust, human in the
  loop) today. Deny-default cron lanes and any MCP-broker access wait on
  per-tool authorization; the broker is not wired to Hermes.
