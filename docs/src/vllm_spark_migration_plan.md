# vLLM on Spark — reasoning/driver serving migration

**Status: COMPLETE — 2026-07-23 → 2026-07-26.** `ollama-spark` was
decommissioned in [#13295]; the vLLM fleet and TEI own the GB10.

This began as a proposal and is retained as the **record of what was actually
done**, because several of its original conclusions turned out to be wrong in
instructive ways. Where a section's plan and its outcome differ, the outcome is
called out inline rather than the section being rewritten — the wrong
predictions are the useful part.

**The one-line correction to the original plan:** the premise that two FP8
models fit co-resident in 128 GB was **false in practice**. The driver alone
holds ~97 GiB. There is one vLLM instance on the Spark, not two, and the coder
moved to the P40 as a small GGUF.

| Outcome | Where it landed |
|---|---|
| Reasoning/driver | `vllm-driver-spark`, Qwen3.6-35B-A3B-FP8, **single-tenant** |
| Coder | ❌ not on the Spark — `qwen2.5-coder:7b` on the **P40** |
| Embeddings | `tei-embed-spark` (bge-m3) — Open WebUI, LightRAG, memory-mcp, Windmill |
| Vision (Frigate) | `qwen2.5vl:3b` on the **P40** |
| `ollama-spark` | **deleted**, ~258 GiB ceph reclaimed, GPU slices 7/8 → 6/8 |

## Summary

Move the Spark's **LLM serving** from Ollama (llama.cpp/GGUF) to a vLLM fleet
(FP8) plus TEI for embeddings and reranking. Ollama serves this model class
inefficiently on the GB10; vLLM roughly **doubles single-stream throughput** and
adds **continuous batching** for the Spark's several concurrent consumers. That
part held up — the throughput case was correct.

> **Superseded as written.** The original text claimed that because the GB10 has
> 128 GB unified memory, "two FP8 vLLM instances fit concurrently". They do not.
> See [Memory budget](#memory-budget-does-it-fit-in-128-gb) for the measured
> numbers and [D5](#d5--coder-model) for where the coder actually went.

[#13295]: https://github.com/rwlove/home-ops/pull/13295

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
  └───────┬───────┘      │  qwen3.6-27b-FP8      /v1                │
          │              └──────────────────────────────────────────┘
          │ embed        ┌──────────────────────────────────────────┐
          ├─────────────▶│ tei-embed-spark  (TEI, bge-m3)  [NEW]     │
          │ rerank       │ tei-spark        (TEI, reranker) [exists] │
          └─────────────▶└──────────────────────────────────────────┘
```

> **The diagram above is the PLANNED end state, not the actual one.**
> `vllm-coder-spark` never ran; the coder lives on the P40. Everything else
> landed as drawn.

Two vLLM instances — driver and coder — run concurrently, each an
OpenAI-compatible `/v1` endpoint, each with its own memory slice of the 128 GB.
GPU *compute* is time-shared between them (fine for a low-traffic fleet where
usually one is hit at a time); *memory* is statically partitioned. Embeddings
move off Ollama to a **new TEI embedding instance** (`bge-m3`), joining the
existing TEI reranker. Vision/occasional models move to the P40 or are dropped.
`ollama-spark` is decommissioned (parked at zero replicas briefly for rollback,
then removed).

### Memory budget (does it fit in 128 GB?)

The GB10's 128 GB is **unified** — shared by the OS, the kubelet, *and every
GPU workload on the node*. The Spark is not dedicated to the vLLM fleet: it
already runs **comfyui-spark, av1corrector, and pump-cv** alongside Ollama and
TEI (confirmed live — 5 of 8 GPU time-slices in use; see below). Their memory
**must** be counted, and my first draft did not.

| Workload | Resident footprint | `--gpu-memory-utilization` (of 128 GB) |
|---|---|---|
| `vllm-driver-spark` — Qwen3.6-35B-A3B-FP8 (35 GB + KV) | ~44 GB | **0.36** |
| `vllm-coder-spark` — Qwen3.6-27B-FP8 (dense, 27 GB + KV) | ~34 GB | **0.28** |
| `tei-embed-spark` + `tei-spark` | ~4 GB | — |
| **comfyui-spark** (idle ~2 GB, **bursts to tens of GB** loading an image-gen model) | **~2–20 GB** | — |
| **pump-cv** (CV model) | ~2 GB | — |
| **av1corrector** (video-encode jobs, bursty) | a few GB when active | — |
| System / OS / kubelet | ~12 GB | — |
| **Total (comfyui idle)** | **~93 GB** | ~0.70 |
| **Total (comfyui mid-generation)** | **~110 GB+** | — |

So it fits **at rest with headroom, but a comfyui image-generation burst while
both vLLM instances are resident can approach or exceed 128 GB.** **Live-tuned
2026-07-23:** vLLM reports **121.63 GiB** usable (not 128 — ~6 GiB
system/firmware), and the non-vLLM co-tenants measured **~32 GiB** (more than
first drafted). `0.42 + 0.35` overshot and the coder failed to init; **driver
cut to 0.36, coder to 0.28** — the pair (~78 GiB) + co-tenants (~32) leaves
**~12 GiB** for comfyui bursts.

> ### ⚠️ OUTCOME: the table above is wrong, and co-residency failed
>
> Even at the retuned `0.36 / 0.28`, the coder could not start. The reason the
> whole budget was wrong is that **`--gpu-memory-utilization` does not bound
> what the process actually holds.** The driver's real resident footprint is
> **~97 GiB**, not the ~44 GiB its 0.36 setting implies, because vLLM's caching
> allocator keeps what it touches.
>
> Confirmed by a reclaim test: stopping the coder **freed nothing**, so this was
> not a leak — the driver genuinely holds ~97 GiB.
>
> ```text
> driver resident   ~97 GiB   (0.36 util "should" be ~44 GiB)
> free for coder    ~24 GiB   vs the ~34 GiB it needs
> ```
>
> `vllm-coder-spark` was parked at `replicas: 0` in
> [#13222](https://github.com/rwlove/home-ops/pull/13222) and the coder moved to
> the P40 (see [D5](#d5--coder-model)). Its manifest is kept parked rather than
> deleted — the tuned FP8 flags are the expensive part to reconstruct if vLLM's
> unified-memory accounting on GB10 ever improves.
>
> **Transferable lesson:** on GB10, budget from *measured resident footprint*
> (`/proc/meminfo` on the host), never from the sum of `gpu-memory-utilization`
> settings. The flag is a request, not a cap.

**Measuring the real number is hard on the GB10** — and that is a first-class
caveat, not a footnote:

- `kubectl top` reads cgroup RSS and **undercounts CUDA allocations** (ollama-spark
  shows ~13 GB RSS while holding ~50 GB of model in unified memory).
- The Spark is not cleanly exposed in node-exporter, and GB10 DCGM `FB_USED` is
  broken (see `gpu-routing.md`).
- The reliable read is **`/proc/meminfo` (`MemAvailable`) on the host** — a
  Phase-3 on-box step, plus watching for pod OOMKills and vLLM startup
  out-of-memory errors.

**Contention policy (decide in Phase 3):** the vLLM fleet and embeddings are the
protected tier; comfyui/av1corrector are best-effort and may need a memory cap
or lower priority so a generation burst can't OOM the reasoning path. `ollama-spark`
scaling to 0 at cutover frees its ~13–50 GB back — but note the **transition
window** (Phase 3, ollama *still resident* while the fleet loads) is the tightest
moment: scale ollama down before bringing the fleet up.

## Key decisions (as made — outcomes noted where they diverged)

### D1 — Decommission `ollama-spark`; the vLLM fleet takes the GPU

The GB10 already time-slices between `ollama-spark` and `tei-spark`. With **two
vLLM instances (driver + coder) co-resident** plus TEI, Ollama has **no
remaining production role** — its multi-model job is covered by the fleet, and
its embeddings move to TEI. **Recommendation:** the vLLM+TEI fleet owns the GPU;
`ollama-spark` scales to 0 (parked in Git for rollback), then is removed after a
stability window. Keep it *only* if you decide the experimentation escape hatch
(easy `ollama pull` of arbitrary new models) is worth a parked deployment —
that is the single remaining reason to retain it (see Open questions).

### D5 — Coder model

> **OUTCOME (2026-07-26): the decision below was reversed.** Qwen3.6-27B-FP8
> never ran — it could not fit alongside the driver (see the memory-budget
> outcome above). The coder is **`qwen2.5-coder:7b` (Q4_K_M, 4.58 GiB measured)
> on the P40**, served by the existing `ai/ollama`, in
> [#13267](https://github.com/rwlove/home-ops/pull/13267).
>
> Pascal (sm_61) has no FP8 and modern vLLM dropped the arch, so GGUF via the
> ollama already on that GPU was the only route — and it added no new component.
> It **replaced** `qwen2.5:7b` rather than being added beside it, keeping the
> P40's resident set at two models; a third 7b-class model would have cut Immich
> CLIP burst headroom to ~2.3 GiB, which is how the 2026-05-18 VRAM-exhaustion
> incident happened.
>
> The "validate it meaningfully beats the driver at coding" caveat below was
> never tested — the memory constraint decided it first.

The original decision follows, for context on why 27B was chosen.

#### D5 (original) — DECIDED: Qwen3.6-27B-FP8

Keeping the coder hot costs a permanent ~27 GB + a second instance.
`qwen3.6-35b-a3b` is itself a strong coder, so this was a deliberate choice, not
a default. **Decided: `Qwen/Qwen3.6-27B-FP8`** — the dense flagship coder (77.2
SWE-bench, matches Opus on Terminal-Bench), replacing the older `qwen2.5-coder:32b`.
Tradeoff accepted: dense → slower single-stream than the MoE driver (reads all
27B/token), bought for higher code quality on the coder/reviewer-agent workload.
Still worth validating on-box that it *meaningfully* beats the driver at coding
before it earns the permanent slice — if it doesn't, drop it and let the driver
do both. (Built in PR 4.)

### D6 — vLLM image (DECIDED: `timothystewart6/vllm-gb10`, digest-pinned)

Reviewed against the sourcing principles: official `vllm/vllm-openai` lacks
sm_121 arm64 (upstream #36821/#31128 open); NVIDIA NGC lags upstream
(disqualifying for a just-released model). **Decided:
`ghcr.io/timothystewart6/vllm-gb10:v0.25.1-cu13.2-torch2.11-gb10.3@sha256:30e70a37…`**
— community sm_121/arm64 image that tracks upstream vLLM directly, SHA-pins every
build input, and is transparent (public GH + CI). Digest-pinned,
`# workaround:`-annotated, and Renovate-held — exactly the `tei-spark`
precedent. Base image has no entrypoint
(`Cmd=bash`) so the HR sets `command: ["vllm","serve"]`.

### D7 — GPU time-slicing (VERIFIED: no change needed)

The gpu-operator advertises **8 time-slices**; **5 are in use** (comfyui-spark,
ollama-spark, tei-spark, pump-cv, av1corrector), leaving **exactly 3 free** for
tei-embed + the two vLLM instances = 8/8. No config bump required. Note this is
slice *scheduling* capacity, independent of the *memory* budget above (the real
constraint).

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

> **All phases closed 2026-07-26.** Actual outcome per phase, since several
> diverged from the plan and two ran out of order:
>
> | Phase | Planned | What happened |
> |---|---|---|
> | 0 Prereqs | pin image + weights | ✅ as planned |
> | 1 TEI embeddings up | additive | ✅ as planned ([#13218]) |
> | 2 Cut embedding consumers | all at once | ✅ but **spread over 3 days** — LightRAG + Open WebUI ([#13263]), Windmill via MCP, memory-mcp last ([#13275]) because it needed an upstream code change first |
> | 3 Deploy driver **+ coder** | both co-resident | ⚠️ **driver only.** Coder could not fit; parked ([#13222]) |
> | 4 Flip GPU + cut LLM consumers | after phase 3 | ⚠️ ran **partly before phase 2** — LightRAG's LLM was cut on 07-24 because a dangling `qwen3.5` reference was breaking ingest |
> | 5 Decommission ollama-spark | after a 1–2 week soak | ✅ done 07-26, **without the soak** — by then it served 0 generate / 0 chat calls in 24h, so there was nothing left to soak |
>
> **Phase ordering was not respected and that was correct.** The plan assumed a
> clean sequence; reality interleaved 2 and 4 because a production breakage
> forced the LLM cut early. Worth remembering that phase numbering in a plan is
> a dependency hint, not a schedule.
>
> Two things the plan never anticipated, both found only by testing:
>
> - **`llama3.2-vision:11b` cannot load on the Spark at all** — `unknown model
>   architecture: 'mllama'` on the arm64/GB10 ollama build. The vision workload
>   that appeared to block decommissioning had in fact never run there.
> - **Frigate GenAI had never worked**, for an unrelated CNP reason, so "migrate
>   vision" turned out to mean "make it work for the first time".
>
> [#13218]: https://github.com/rwlove/home-ops/pull/13218
> [#13222]: https://github.com/rwlove/home-ops/pull/13222
> [#13263]: https://github.com/rwlove/home-ops/pull/13263
> [#13275]: https://github.com/rwlove/home-ops/pull/13275

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

- **Scale `ollama-spark` down first** to free its ~13–50 GB — the transition
  window (ollama resident *and* the fleet loading) is the tightest memory moment.
  Ollama serving is briefly degraded; do this in a window.
- Deploy **both** vLLM instances with their capped `--gpu-memory-utilization`.
- **Validate unified-memory co-residency on-box — the riskiest step, and it must
  account for the co-resident non-vLLM workloads (comfyui-spark, av1corrector,
  pump-cv), not just the fleet:**
  - Read true usage via **`/proc/meminfo` `MemAvailable` on the Spark host** (not
    `kubectl top` — it undercounts CUDA on the GB10; not DCGM `FB_USED` — broken).
  - Confirm both vLLM instances load and stay resident without OOMKill.
  - **Stress the worst case:** trigger a comfyui image-generation *while* both
    vLLM instances are resident and under load — this is the burst that can blow
    the budget. If it OOMs the reasoning path, lower the vLLM `--gpu-memory-utilization`
    and/or cap comfyui's memory (D-contention).
- Benchmark: driver hits **~28–30 tok/s single-stream** + concurrency scaling;
  coder loads and serves; tool-calling validated via a real opencode session.
- **Gate:** both instances co-resident and correct through a comfyui burst;
  driver hits the throughput target; `MemAvailable` stays positive with margin.

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
    ├── helmrelease.yaml      # app-template; ghcr.io/timothystewart6/vllm-gb10 (digest-pinned)
    ├── pvc.yaml              # ceph-block, FP8 weights (~35 Gi)
    ├── cnp-allow.yaml        # Cilium policy (consumers → :8000)
    ├── servicemonitor.yaml   # vLLM native Prometheus metrics
    └── prometheusrule.yaml   # queue depth / TTFT / model-loaded alerts

kubernetes/apps/ai/vllm-coder-spark/   # NEW — Qwen3.6-27B-FP8, second instance
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
| **Fleet OOMs the 128 GB** (tightest risk) | Explicit capped `--gpu-memory-utilization` (driver 0.40 / coder 0.30) + FP8 KV + tuned `max-model-len`; validate co-residency on-box in Phase 3 via host `/proc/meminfo` before cutover |
| **comfyui-spark image-gen burst OOMs the reasoning path** (the co-resident workload the first draft missed) | comfyui/av1corrector are best-effort; cap their memory or lower priority so a burst can't evict vLLM; explicitly stress-test a comfyui burst against the resident fleet in Phase 3 |
| GPU contention during Phase 3 coexistence with Ollama | Scale Ollama down **first** (transition window is tightest); keep coexistence brief; flip in a window |
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

## Open questions for Rob — ALL RESOLVED

Kept with their answers; the reasoning is more useful than the questions.

1. **Which coder model stays hot?** → *Neither of the options offered.* The
   memory constraint decided it before quality could be benchmarked:
   `qwen2.5-coder:7b` on the **P40**, replacing `qwen2.5:7b`
   ([#13267](https://github.com/rwlove/home-ops/pull/13267)). Heavy coding
   rides the driver, which is itself a strong coder.
2. **Keep `ollama-spark` parked as an experimentation hatch?** → **No, removed**
   ([#13295](https://github.com/rwlove/home-ops/pull/13295)). The hatch argument
   lost to evidence: in its final 24h it served 0 `/api/generate`, 0 `/api/chat`
   and 290 `/api/embed` — almost exactly the 288 calls its own monitoring probe
   made. The P40 `ollama` still provides `ollama pull` experimentation.
3. **Memory split tuning (0.40 / 0.35)?** → **Moot.** There is one instance.
   Driver sits at `0.36`; the number is nearly cosmetic given the allocator
   holds ~97 GiB regardless.
4. **Vision — move to P40 or drop?** → **Moved to the P40**, but not as planned:
   `llama3.2-vision:11b` proved **unloadable on the Spark entirely**
   (`unknown model architecture: 'mllama'` on the arm64/GB10 build), so it was
   replaced by `qwen2.5vl:3b` rather than relocated. Separately, Frigate's GenAI
   had **never worked** due to an asymmetric CNP — so this became "make it work
   for the first time", not "migrate it".
5. **Concurrency vs single-stream sizing?** → **Single-stream, implicitly.**
   `max-num-seqs 32` and 64k context were chosen to make CUDA-graph capture
   succeed
   ([#13226](https://github.com/rwlove/home-ops/pull/13226)), not from a
   concurrency target. Revisit if the driver ever sees real parallel load.

## What this migration actually taught

Worth carrying to the next GPU-capacity plan:

- **`--gpu-memory-utilization` is a request, not a cap.** Budget from measured
  resident footprint on the host, never from the sum of the settings. The gap
  here was ~44 GiB predicted vs ~97 GiB actual.
- **Unified memory means every co-tenant counts.** The first draft omitted
  comfyui/pump-cv/av1corrector entirely and was wrong by ~32 GiB.
- **Phase numbers are a dependency graph, not a schedule.** Phase 4 ran before
  phase 2 because a production breakage forced it, and that was the right call.
- **Test the thing you are migrating before planning around it.** Two workloads
  (`llama3.2-vision`, Frigate GenAI) turned out to be non-functional *before*
  the migration touched them. Both were treated as constraints for days.
- **A workload whose only traffic is its own monitoring probe is already dead.**
  That single measurement retired a service the plan had allocated a two-week
  soak to.

## Data classification

This document is **Internal-tier** cluster architecture rendered to the public
docs site. It contains no secrets, media/library names, or the restricted media
stack, and uses roles/`${SECRET_DOMAIN}` rather than specific hostnames — vLLM
on a DGX Spark is widely-documented public territory. If Rob prefers this stay
internal-only, relocate it out of the `nav:` (or to the vault) before merge.
