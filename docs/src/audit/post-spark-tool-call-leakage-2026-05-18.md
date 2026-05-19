# Post-Spark tool-call-JSON leakage audit (2026-05-18)

**Phase 0d acceptance criterion 5** for the langgraph-agents activation roadmap.
Verifies that both Ollama-backed models in the dual-concurrent stack emit
tool-calls via the proper `tool_calls` channel rather than leaking raw
`{"name": ..., "arguments": ...}` JSON into the assistant message text — the
historic qwen2.5:14b-era bug that motivated the audit in the first place.

## Stack under test

| Service | Endpoint | Node | Model | Role |
|---|---|---|---|---|
| `ollama` | `ollama.ai.svc.cluster.local:11434` | worker8 (P40, 24 GiB VRAM) | `qwen2.5:7b` Q4_K_M | `local-p40` group — light/mechanical agents |
| `ollama-spark` | `ollama-spark.ai.svc.cluster.local:11434` | spark (Blackwell GB10, 128 GiB unified) | `qwen2.5:32b` Q4_K_M | `local-spark` group — reasoning/structured-output agents |

Per agent factory at `agents.llm.AGENT_GROUP`: heavy agents (coder, reviewer,
homelab-engineer, network/storage/smart-home/ml/observability operators) route
to Spark; light agents (triager, reporter, note-maker, researcher,
errand-runner, supervisor, property-coordinator, health-tracker, doc-writer)
route to P40.

## Methodology

Two probe payloads against `POST /api/chat` on each Ollama Service:

1. **Tool-call probe** — sends a `tools: [{type: function, function: get_current_weather, …}]` definition plus a user message that demands the tool be invoked ("What is the current weather in Boston, MA?"). Pass = response has `message.content == ""` AND `message.tool_calls[0].function.name == "get_current_weather"` with correct args. Fail = JSON blob leaks into `message.content`.
2. **Plain probe (control)** — same model, simple "Say hello in one word." prompt, no `tools` field. Pass = `message.content` is a non-empty short reply. Confirms inference path works.

Probes issued via `curl` through `kubectl port-forward` so they hit the real
in-cluster Service endpoints (not a localhost ollama).

## Spark — `qwen2.5:32b` on Blackwell

**Tool-call probe**:
```json
{
  "model": "qwen2.5:32b",
  "created_at": "2026-05-18T23:56:04.956578363Z",
  "message": {
    "role": "assistant",
    "content": "",
    "tool_calls": [
      {
        "id": "call_n5qxh6ho",
        "function": {
          "index": 0,
          "name": "get_current_weather",
          "arguments": {"location": "Boston, MA"}
        }
      }
    ]
  },
  "done": true,
  "done_reason": "stop",
  "total_duration": 2.50s,
  "prompt_eval_count": 163,
  "eval_count": 23,
  "eval_duration": 2.11s
}
```

**Verdict: ✅ CLEAN.**
- `content` is empty — no leakage.
- `tool_calls` array is populated with the correct function name and correctly-shaped arguments (`{"location": "Boston, MA"}`).
- Eval rate ~11 tok/s warm (23 tokens in 2.1s) — typical for 32b on Blackwell with KV cache q8_0.

**Plain probe**:
```json
{
  "model": "qwen2.5:32b",
  "message": {"role": "assistant", "content": "Hello!"},
  "done": true,
  "total_duration": 0.41s
}
```

Working inference path, ~0.4s warm.

## P40 — `qwen2.5:7b` on worker8

### Initial state (pre-tune)

`/api/chat` hung past 60s timeout; subsequent retries returned HTTP 500.
Pod logs from `ollama-0` showed every `/api/chat` and `/v1/chat/completions`
since 19:55 EDT returning 500, including Open WebUI traffic.

A fresh pod restart at 19:59 EDT brought the pod back Ready in 10s, but
cold model-load took **48 seconds** and the first `/api/generate` after
restart took **9m18s** to complete a `"hi"` prompt — indicating significant
CPU spillover. Logs from `llama_context` showed:

```
llama_context:        CUDA0 compute buffer size =   730.36 MiB
llama_context:  CUDA_Host compute buffer size =    39.01 MiB
llama_kv_cache:        CPU KV buffer size =  1904.00 MiB
llama_context: graph splits = 396 (with bs=512)
```

KV cache landed on CPU rather than VRAM (1.9 GiB on host RAM), and the
compute graph splits across CPU↔GPU 396 times per batch.

### Root cause + fix (home-ops PR #11640)

`OLLAMA_NUM_PARALLEL=4` (recent Ollama default) × `OLLAMA_CONTEXT_LENGTH=16384`
with KV-cache q8_0 reserved ~7.6 GiB VRAM for KV slots alone. Combined with
qwen2.5:7b weights (~4.7 GiB) and immich-machine-learning workloads on the
same node, total VRAM demand exceeded the 24 GiB P40 budget; ollama silently
fell back to CPU offload.

Tune applied via PR #11640:
- `OLLAMA_NUM_PARALLEL`: 4 → 1
- `OLLAMA_CONTEXT_LENGTH`: 16384 → 8192
- Added `install.disableWait` / `upgrade.disableWait` (cold-start exceeds 5min)

### Post-tune state — TOOL-CALL CLEAN ✅

After PR #11640 reconciled and `ollama-0` restarted, log inspection confirms
GPU-resident inference:

```
llama_kv_cache:      CUDA0 KV buffer size =   229.50 MiB
llama_kv_cache: size = 238.00 MiB (8192 cells, 28 layers, 1/1 seqs)
llama_context:      CUDA0 compute buffer size =   730.36 MiB
llama_context:  CUDA_Host compute buffer size =    23.01 MiB
llama_context: graph splits = 18 (with bs=512), 3 (with bs=1)
```

KV cache on CUDA0 (was on CPU), graph splits reduced 396 → 18.

**Tool-call probe** (against the post-tune pod):
```json
{
  "model": "qwen2.5:7b",
  "created_at": "2026-05-19T00:20:32.72490431Z",
  "message": {
    "role": "assistant",
    "content": "",
    "tool_calls": [
      {
        "id": "call_oqim0ssa",
        "function": {
          "index": 0,
          "name": "get_current_weather",
          "arguments": {"location": "Boston, MA"}
        }
      }
    ]
  },
  "done": true,
  "done_reason": "stop",
  "total_duration": 19.77s,
  "load_duration": 0.40s,
  "prompt_eval_count": 163,
  "prompt_eval_duration": 0.39s,
  "eval_count": 23,
  "eval_duration": 3.80s
}
```

**Verdict: ✅ CLEAN.** Content empty, structured `tool_calls` with correct
function name and arguments. Eval rate ~6 tok/s on P40 (23 tokens in 3.8s) —
typical for qwen2.5:7b Q4_K_M on Pascal. Total wall-time 19.8s includes some
prompt-eval overhead unique to first tool-call cycle; subsequent calls would
be faster on the warm pod.

## Implications for Phase 0d activation

Both ollama Services now serve clean tool-calls on their assigned models.
**Phase 0d criterion 5 satisfied for both groups.** Activation
(`LANGGRAPH_TRIGGERS_ENABLED=true`) is unblocked from a tool-call perspective.

## Open follow-ups

- [x] **P40 spill investigation** — root cause identified + fix shipped via
  PR #11640.
- [x] **Re-probe qwen2.5:7b for tool-call leakage** after P40 tune — clean.
- [ ] **Image-tag rollout**: the langgraph-agents HR pod still runs v0.2.5;
  PR #11641 (this PR) bumps to v0.2.6 which contains the Phase 2 factory.
- [ ] **Activation flip**: `LANGGRAPH_TRIGGERS_ENABLED=true` (separate PR
  after this one lands and the new pod is verified Running).
