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

## P40 — `qwen2.5:7b` (degraded — investigation deferred)

**Tool-call probe**: hung past 60s timeout; subsequent retries returned HTTP 500
on `/api/chat`. Pod logs from `ollama-0` show every `/api/chat` and
`/v1/chat/completions` since 19:55 EDT returning 500, including Open WebUI
traffic from `kubeclaw-gateway`.

A fresh pod restart at 19:59 EDT (`kubectl -n ai delete pod ollama-0`) brought
the pod back Ready in 10s, but the subsequent cold model-load took **48 seconds**
(per `llama runner started in 48.43 seconds` log) — far slower than expected.
The first `/api/generate` after restart took **9m18s** to complete a `"hi"`
prompt, indicating significant CPU spillover. Logs from `llama_context` show:

```
llama_context:        CUDA0 compute buffer size =   730.36 MiB
llama_context:  CUDA_Host compute buffer size =    39.01 MiB
llama_kv_cache:        CPU KV buffer size =  1904.00 MiB
llama_context: graph splits = 396 (with bs=512)
```

KV cache landed on CPU rather than VRAM (1.9 GiB on host RAM), and the compute
graph splits across CPU↔GPU 396 times per batch — that's pathological for
inference latency.

### Likely root cause (unverified — needs follow-up)

P40 has 24 GiB VRAM. qwen2.5:7b Q4_K_M weights ~4.7 GiB. With
`OLLAMA_NUM_PARALLEL=4` (recent Ollama default) and `OLLAMA_CONTEXT_LENGTH`
unset (inheriting Ollama's higher default), KV cache reservation balloons:
4 slots × ~1.9 GiB per slot ≈ 7.6 GiB. Add immich-machine-learning's CLIP +
embedding models on the same node, and the P40 runs out of VRAM headroom.
Ollama spills to CPU instead of failing outright.

### Verdict + recommendation

**Tool-call leakage**: undetermined for qwen2.5:7b — the inference path itself
is degraded enough that the tool-call test isn't meaningful right now.

**Action**:
1. Do NOT flip `LANGGRAPH_TRIGGERS_ENABLED=true` yet — light agents would
   queue on the degraded P40 path.
2. Tune ollama HR: pin `OLLAMA_NUM_PARALLEL=1` and `OLLAMA_CONTEXT_LENGTH` to
   a tight value (e.g. 8192) to fit KV cache fully into VRAM. Re-test.
3. If still spilling: identify which immich-machine-learning model is holding
   most VRAM and consider relocating one replica to spark (immich-ML is the
   ideal candidate for Blackwell — 8 × time-sliced nvidia.com/gpu).
4. After P40 retest passes, re-run this audit's tool-call probe on
   qwen2.5:7b and update this doc.

Spark side is unaffected — `local-spark` agents can activate independently
once Phase 2 factory image rolls out to the langgraph-agents Pod.

## Implications for Phase 0d activation

- Spark (heavy agents): cleared for activation. Phase 2 factory routes the
  8 heavy agents to `ollama-spark` cleanly.
- P40 (light agents): blocked on the VRAM/CPU-spill issue above.

**Recommendation**: ship the home-ops CNP egress widening (this PR), then
**investigate + fix the P40 spill** before flipping `LANGGRAPH_TRIGGERS_ENABLED`.
The flip activates ALL agents — half of which route to the degraded P40 — so
a partial flip isn't possible without a code change to AGENT_GROUP.

Alternative: temporarily widen `AGENT_GROUP` to route every agent to
`local-spark` (Blackwell has the VRAM headroom for both groups concurrently)
as a stopgap until P40 is tuned. Document the override in the next iteration
of the plan and revert once P40 is healthy.

## Open follow-ups

- [ ] **P40 spill investigation** — see "Likely root cause" above. Tune
  `OLLAMA_NUM_PARALLEL` + `OLLAMA_CONTEXT_LENGTH`, possibly relocate immich-ml
  replica to spark.
- [ ] **Re-probe qwen2.5:7b for tool-call leakage** after P40 tune; update
  this doc with the result.
- [ ] **Decide on stopgap**: route everything to Spark until P40 tuned, or
  keep AGENT_GROUP as-is and defer activation entirely.
