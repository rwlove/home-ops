# vLLM on Spark — reasoning/driver serving migration (plan)

**Status:** Proposal — not yet approved or executed. Nothing in the running
inference path changes until Rob signs off phase-by-phase.

## Summary

Move the Spark's **LLM serving** from Ollama (llama.cpp/GGUF) to a **two-model
vLLM fleet (FP8)** — the reasoning/driver model and the coder model both kept
hot and co-resident in the GB10's 128 GB unified memory — plus TEI for
embeddings and reranking. Ollama serves this model class inefficiently on the
GB10; vLLM roughly **doubles single-stream throughput** and adds **continuous
batching** for the Spark's several concurrent consumers.

Because the GB10 has 128 GB unified memory (not a 24 GB discrete card), two
FP8 vLLM instances fit concurrently — so this **fully replaces** Ollama's
production role rather than trading away its multi-model juggling. Ollama is
decommissioned (kept parked briefly for rollback). Embeddings/vision move to
TEI/P40.

## Motivation

Measured on-box (2026-07-23), single-stream decode of `qwen3.6:35b-a3b` (q4) on
Ollama:

| Metric | Value | Notes |
|---|---|---|
| Steady-state generation | **~15–16 tok/s** | consistent across runs |
| Effective memory bandwidth | **~11% of peak** | ~31 GB/s of ~273 GB/s |
| GPU throttle counters | **0 µs** (never) | not a hardware/thermal/power fault |
| GPU temp under load | 56 °C | large thermal headroom |

The GB10 is **healthy** — nothing is throttling it. The bottleneck is
Ollama/llama.cpp's **MoE handling** on this architecture. On-box control: a
*dense* model (`qwen2.5-coder:32b`) reaches ~31% of peak bandwidth, but the
*MoE* only reaches ~11% — same GPU, same runtime. External benchmarks confirm
the same `Qwen3.6-35B-A3B` on **vLLM (FP8) reaches ~28–30 tok/s single-stream**
(the actual memory-bandwidth ceiling) and **~156 tok/s aggregate at
concurrency 32**. So there is a real, verified ~1.8× single-stream win plus
large concurrency headroom being left on the table.

NVFP4 (Blackwell-native 4-bit) projects a further ~2× over FP8, but its MoE
kernels on GB10 are immature as of 2026-04 — **plan on FP8, treat NVFP4 as
later upside.**

## Current state

```text
                         Spark node (arm64, GB10, sm_121)
                         GPU time-sliced (device plugin advertises >1)
  ┌───────────────┐      ┌──────────────────────────────────────────┐
  │ Open WebUI    │─chat▶│ ollama-spark  (Ollama, GGUF, many models) │
  │ LightRAG      │─LLM─▶│  - qwen3.6/2.5 reasoning/driver           │
  │ opencode      │─────▶│  - qwen2.5-coder                          │
  │ memory-mcp    │─embed│  - bge-m3 / nomic  (EMBEDDINGS)           │
  │ paperless RAG │─embed│  - llama3.2-vision (on-demand)            │
  └───────┬───────┘      └──────────────────────────────────────────┘
          │ rerank       ┌──────────────────────────────────────────┐
          └─────────────▶│ tei-spark  (TEI, bge-reranker-v2-m3)      │
                         └──────────────────────────────────────────┘
```

- **`ollama-spark`** — `kubernetes/apps/ai/ollama-spark/`, one GPU slice,
  serves the reasoning/driver model, the coder, **the embedders**, and vision.
- **`tei-spark`** — already on the Spark for reranking (see
  [tei_spark.md](tei_spark.md)); proves TEI works here and that the GB10 is
  already GPU-shared between two workloads.
- **Consumers** all speak OpenAI-compatible or Ollama HTTP: `open-webui`,
  `lightrag`, `opencode`, `memory-mcp`, the paperless-RAG Windmill flow.

## Target architecture

```text
                         Spark node (arm64, GB10, sm_121, 128 GB unified)
  ┌───────────────┐      ┌──────────────────────────────────────────┐
  │ Open WebUI    │─chat▶│ vllm-driver-spark (vLLM FP8)              │
  │ LightRAG      │─LLM─▶│  qwen3.6-35b-a3b-FP8   /v1               │
  │ opencode      │──┬──▶└──────────────────────────────────────────┘
  │  (driver)     │  │   ┌──────────────────────────────────────────┐
  │  (coder)      │  └──▶│ vllm-coder-spark  (vLLM FP8)              │
  └───────┬───────┘      │  <coder>-FP8          /v1                │
          │              └──────────────────────────────────────────┘
          │ embed        ┌──────────────────────────────────────────┐
          ├─────────────▶│ tei-embed-spark  (TEI, bge-m3)  [NEW]     │
          │ rerank       │ tei-spark        (TEI, reranker) [exists] │
          └─────────────▶└──────────────────────────────────────────┘
```

Two vLLM instances — driver and coder — run concurrently, each an
OpenAI-compatible `/v1` endpoint, each with its own memory slice of the 128 GB.
GPU *compute* is time-shared between them (fine for a low-traffic fleet where
usually one is hit at a time); *memory* is statically partitioned. Embeddings
move off Ollama to a **new TEI embedding instance** (`bge-m3`), joining the
existing TEI reranker. Vision/occasional models move to the P40 or are dropped.
`ollama-spark` is decommissioned (parked at zero replicas briefly for rollback,
then removed).

### Memory budget (does it fit in 128 GB?)

Unified memory is shared with the OS/kubelet; the Spark runs only GPU workloads,
so reserve ~12 GB for system and budget ~116 GB for inference.

| Instance | Weights (FP8) | KV + activations | `--gpu-memory-utilization` (of 128 GB) |
|---|---|---|---|
| `vllm-driver-spark` (35B-A3B) | ~35 GB | ~16 GB | ~0.40 (~51 GB) |
| `vllm-coder-spark` (~32B) | ~32 GB | ~13 GB | ~0.35 (~45 GB) |
| `tei-embed-spark` + `tei-spark` | ~2 GB | small | ~0.03 |
| **Total** | | | **~0.78 (~100 GB), ~16 GB headroom** |

Fits, but the utilizations must be **explicitly capped** so the two vLLM
instances don't each grab 90%. `max-model-len` is tuned to keep KV modest.
This is the tightest part of the design and gets validated on-box in Phase 3.

## Key decisions (recommendations inline — Rob confirms)

### D1 — Decommission `ollama-spark`; the vLLM fleet takes the GPU

The GB10 already time-slices between `ollama-spark` and `tei-spark`. With **two
vLLM instances (driver + coder) co-resident** plus TEI, Ollama has **no
remaining production role** — its multi-model job is covered by the fleet, and
its embeddings move to TEI. **Recommendation:** the vLLM+TEI fleet owns the GPU;
`ollama-spark` scales to 0 (parked in Git for rollback), then is removed after a
stability window. Keep it *only* if you decide the experimentation escape hatch
(easy `ollama pull` of arbitrary new models) is worth a parked deployment —
that is the single remaining reason to retain it (see Open questions).

### D5 — Which coder model, and confirm it earns a permanent slice

Keeping the coder hot costs a permanent ~32 GB + a second instance.
`qwen3.6-35b-a3b` is itself a strong coder, so the separate coder is partly
redundant — worth a deliberate choice rather than defaulting to the current
`qwen2.5-coder:32b` (older gen). **Recommendation:** if the coder stays,
consider upgrading it to a current-gen Qwen coder in FP8 rather than carrying
2.5; validate it beats the driver on the coder/reviewer-agent workload before
committing the slice. If it doesn't clearly beat the driver, drop it and let the
driver do both.

### D2 — Embeddings → new TEI instance (`bge-m3`), not a minimal Ollama

Embeddings are on the critical path (memory-mcp, paperless RAG, Open WebUI RAG).
Keeping a "just embeddings" Ollama alive re-introduces the GPU-contention
problem. **Recommendation:** deploy `tei-embed-spark` (TEI serving `bge-m3`,
mirroring the existing reranker deployment). TEI is efficient and already
proven on this node. `nomic-embed-text` is retired (bge-m3 already won the
Phase A eval).

### D3 — FP8 now, NVFP4 later

FP8 is production-ready on GB10 and delivers the verified ~28–30 tok/s. NVFP4
kernels are immature. **Recommendation:** ship FP8; open a tracking issue to
revisit NVFP4 when GB10 kernels mature.

### D4 — Model: `Qwen3.6-35B-A3B-FP8`

Same model family already chosen as the opencode driver; benchmarked on GB10 by
the community; best agentic tool-calling in its class. One model serves
open-webui + LightRAG + opencode.

## Migration phases

Each phase has a validation gate; do not proceed until it passes. Disruptive
steps run in a **routine maintenance window (02:00–05:00 ET)** — the affected
services (Open WebUI, LightRAG) are routine-tier, not Renee-facing.

### Phase 0 — Prerequisites (no cluster change)

- Confirm a vLLM container image with **GB10/sm_121 arm64** support and a pinned
  digest (community guides exist for this exact model on GB10).
- Confirm the **FP8 weights** source (HF repo) and licensing.
- Confirm `tei-embed-spark` can serve `bge-m3` (TEI embedding variant; the
  reranker deployment is the template).
- **Gate:** image + weights + TEI-embed path identified and pinned.

### Phase 1 — Stand up embeddings on TEI (additive, non-disruptive)

- Deploy `tei-embed-spark` (`bge-m3`) alongside everything. No consumer changes
  yet.
- **Gate:** `/embed` returns correct 1024-dim vectors; latency acceptable.

### Phase 2 — Cut embedding consumers to TEI

- Repoint Open WebUI `RAG_OLLAMA_BASE_URL`, LightRAG `EMBEDDING_BINDING_HOST`,
  memory-mcp, and the paperless-RAG flow to `tei-embed-spark`.
- **Gate:** RAG retrieval quality unchanged (spot-check a few queries); the
  synthetic embed probe passes against TEI. Ollama embeddings now unused.

### Phase 3 — Deploy the vLLM fleet (driver + coder, FP8), Ollama still primary

- Deploy **both** vLLM instances with their capped `--gpu-memory-utilization`
  so they bring up and self-test while Ollama is scaled down enough to free
  memory (Ollama serving is briefly degraded — do this in a window).
- **Validate the memory budget on-box first** — this is the riskiest step:
  confirm both instances load and stay resident without OOM, and that ~16 GB
  headroom holds under load.
- Benchmark: confirm the driver hits **~28–30 tok/s single-stream** and
  concurrency scaling; confirm the coder loads and serves; validate tool-calling
  via a real opencode session against the driver.
- **Gate:** both instances co-resident and correct; driver hits the throughput
  target; memory headroom stable.

### Phase 4 — Flip the GPU allocation + cut LLM consumers over

- Scale `ollama-spark` to 0; the vLLM fleet + TEI own the GPU.
- Repoint Open WebUI (new OpenAI connection(s) — one per model), LightRAG
  (`LLM_BINDING_HOST`/`LLM_MODEL` → driver), and opencode (`ollama-spark`
  provider `baseURL` → the two vLLM endpoints; opencode already speaks
  OpenAI-compatible).
- **Gate:** all consumers generate correctly against vLLM; the coder is
  selectable; LightRAG ingest completes; Open WebUI default model resolves.

### Phase 5 — Decommission or park `ollama-spark`

- Keep `ollama-spark` scaled to 0 in Git for one to two weeks as rollback
  insurance, then remove the manifests + PVC if vLLM proves stable.
- **Gate:** two weeks stable → delete; or keep parked if the multi-model
  flexibility turns out to be needed.

## home-ops implementation

Standard GitOps app, mirroring `ollama-spark` / `tei-spark`:

```text
kubernetes/apps/ai/vllm-driver-spark/  # NEW — qwen3.6-35b-a3b-FP8
├── ks.yaml
└── app/
    ├── helmrelease.yaml      # app-template; vllm/vllm-openai:<gb10-arm64 digest>
    ├── pvc.yaml              # ceph-block, FP8 weights (~35 Gi)
    ├── cnp-allow.yaml        # Cilium policy (consumers → :8000)
    ├── servicemonitor.yaml   # vLLM native Prometheus metrics
    └── prometheusrule.yaml   # queue depth / TTFT / model-loaded alerts

kubernetes/apps/ai/vllm-coder-spark/   # NEW — <coder>-FP8, second instance
└── app/ ...                          # same shape; capped gpu-memory-utilization

kubernetes/apps/ai/tei-embed-spark/    # NEW — bge-m3 embeddings on TEI
└── app/ ...                          # clone of tei-spark, model=bge-m3
```

Both vLLM instances set an **explicit, capped** `--gpu-memory-utilization`
(per the memory budget) so they coexist without either grabbing the whole GPU.

- **Placement:** `nodeSelector: blackwell` + arm64 toleration +
  `ai-gpu-critical` priority (same as ollama-spark/tei-spark).
- **Weights fetch:** an init/Job `hf download` into the PVC (declarative,
  digest-or-revision pinned) — same idea as the model-sync mechanism discussed
  for Ollama.
- **Observability:** vLLM exposes far richer metrics than Ollama (running/
  waiting requests, batch size, TTFT, cache hit rate) — a real upgrade for the
  Spark dashboards and alerting.
- **Image pin:** if the GB10 image is built from `main` (as the TEI 121- image
  is), pin with a `# workaround:` annotation and a tracking issue, per repo
  convention.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| vLLM GB10 image maturity (built from `main`, arm64/sm_121) | Pin digest + `# workaround:`; community runs this exact model on GB10 |
| **Two instances OOM the 128 GB** (tightest risk) | Explicit capped `--gpu-memory-utilization` per instance + tuned `max-model-len`; validate co-residency on-box in Phase 3 before cutover |
| GPU contention during Phase 3 coexistence with Ollama | Scale Ollama down to free memory; keep coexistence brief; flip in a window |
| Compute time-sharing between the two vLLM instances | Acceptable for low-traffic fleet (usually one hit at a time); revisit if both go hot simultaneously |
| FP8 weights quality vs q4 | Validate output quality in Phase 3 before cutover |
| Embedding regression on TEI | Phase 2 gate: RAG quality spot-check + probe before retiring Ollama embeddings |
| NVFP4 temptation | Explicitly out of scope; tracked separately until kernels mature |

## Rollback

Each phase is independently reversible:

- Phases 1–2 (embeddings): repoint consumers back to `ollama-spark`; delete
  `tei-embed-spark`.
- Phases 3–4 (LLM): scale `ollama-spark` back up, give it the GPU, repoint
  consumers back. vLLM manifests stay in Git, scaled to 0.
- Phase 5: only delete after the parking period.

## Open questions for Rob

1. **D5 — which coder model** stays hot: keep `qwen2.5-coder:32b`, upgrade to a
   current-gen Qwen coder (FP8), or drop the coder and let the driver do both
   (if it benchmarks close)? Decided: *coder stays hot* — model TBD.
2. **Experimentation hatch** — is easy `ollama pull` of arbitrary new models
   worth keeping `ollama-spark` parked (scale-0) indefinitely, or fully remove
   it once the fleet is stable? This is now the *only* reason to retain Ollama.
3. **Memory split** — the ~0.40 / 0.35 utilization split is a starting point;
   tune toward whichever instance (driver vs coder) carries more load.
4. **Vision** (`llama3.2-vision`) — move to P40, or drop until needed?
5. **Concurrency target** — size the driver for peak concurrent consumers, or
   optimize single-stream latency first?

## Data classification

This document is **Internal-tier** cluster architecture rendered to the public
docs site. It contains no secrets, media/library names, or the restricted media
stack, and uses roles/`${SECRET_DOMAIN}` rather than specific hostnames — vLLM
on a DGX Spark is widely-documented public territory. If Rob prefers this stay
internal-only, relocate it out of the `nav:` (or to the vault) before merge.
