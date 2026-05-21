# TEI on Spark — reranker for RAG

`tei-spark` is a HuggingFace `text-embeddings-inference` deployment on the Spark node, serving `BAAI/bge-reranker-v2-m3` as an external cross-encoder for Open WebUI's RAG pipeline. It replaces Open WebUI's in-process reranker so the chat pod doesn't carry a 6 Gi GPU-less-reranker memory footprint and so reranking runs on Spark next to the bge-m3 embedder.

| | |
|---|---|
| **Upstream image** | `ghcr.io/huggingface/text-embeddings-inference:121-sha-<sha>` (digest-pinned) |
| **Model** | `BAAI/bge-reranker-v2-m3` |
| **Manifests** | `kubernetes/apps/ai/tei-spark/` |
| **Cluster service** | `tei-spark.ai.svc.cluster.local:3000` (HTTP `/rerank`, `/metrics`) |
| **Node** | `spark.thesteamedcrab.com` (arm64, GB10, sm_121) |
| **GPU** | `nvidia.com/gpu: 1`, `ai-gpu-critical` priority |
| **Model cache** | 5 Gi `ceph-block` PVC (`tei-spark-cache-pvc`) at `/data` |

## Why this shape

**Family-matched reranker.** `bge-reranker-v2-m3` is BAAI's v2 reranker designed to pair with the `bge-m3` embedder (Phase A 2026-05-20). On BAAI's published MIRACL/BEIR/CMTEB benchmarks reranking `bge-m3` top-100 retrievals, v2-m3 beats `bge-reranker-large` in every multilingual setting. Don't substitute to a v1-family reranker for "TEI tier-1 supported" reasons (see Quirks).

**Spark, not P40.** The P40 is Pascal (sm_61). bge-reranker-v2-m3 wants FP16; Pascal handles FP16 poorly. Spark's Blackwell (sm_121) is the right home, and co-locating with the bge-m3 embedder eliminates a network hop.

**External, not in-process.** Open WebUI's in-process reranker shipped with a 6 Gi memory limit headroom even though the model wasn't always loaded. Pulling it out cleans the architecture for the day Open WebUI's KB external-Qdrant gap closes and rerank actually becomes hot.

## Architecture

```text
                  ┌────────────────────────────┐
   Open WebUI ───▶│ tei-spark (Service:3000)   │
                  │ - /rerank                  │
                  │ - /health                  │
                  │ - /metrics                 │
                  └──────────┬─────────────────┘
                             │ runs on
                             ▼
                  ┌────────────────────────────┐
                  │ Spark node (arm64, GB10)   │
                  │ TEI HTTP variant           │
                  │ Candle CUDA backend        │
                  │ FlashBert on sm_121        │
                  │ dtype=float16              │
                  └──────────┬─────────────────┘
                             │ HF Hub download (first start only,
                             │ cached on ceph-block PVC)
                             ▼
                  ┌────────────────────────────┐
                  │ BAAI/bge-reranker-v2-m3    │
                  │ XLM-RoBERTa-base (568M)    │
                  └────────────────────────────┘
```

## Quirks

### 1. The 121- image is built from `main`, not a tagged release

Upstream PRs [#840](https://github.com/huggingface/text-embeddings-inference/pull/840) (multi-arch CUDA, sm_121) and [#852](https://github.com/huggingface/text-embeddings-inference/pull/852) (restrict 12.1 to linux/arm64) merged 2026-03-31 but no TEI release tag has been cut since `v1.9.3` (pre-#840). The HelmRelease pins to `121-sha-<sha>@sha256:...` under a `# workaround:` annotation. Remove the pin when TEI cuts a release containing #840.

### 2. `bge-reranker-v2-m3` is not on TEI's documented supported-rerankers list

TEI's `docs/source/en/supported_models.md` lists `bge-reranker-base` / `bge-reranker-large` / GTE rerankers — not v2-m3. **It still loads cleanly** because TEI's `router/src/lib.rs::get_backend_model_type` dispatches XLM-RoBERTa Sequence Classification generically via `arch.ends_with("Classification")`; the Candle CUDA backend implements the forward pass. [Issue #713](https://github.com/huggingface/text-embeddings-inference/issues/713) confirms it serving live on TEI 1.8.

If TEI ever tightens the architecture allowlist, v2-m3 could break — keep an eye on release notes. Fallback options in priority order:

1. **Same model, different server** — `michaelfeil/infinity` lists v2-m3 in tested models, Blackwell support landed 2025-07.
2. **Different model, same server** — `bge-reranker-large` (TEI tier-1 listed) but it's the older v1 family and worse on multilingual.

### 3. `/metrics` is on the HTTP port, not `--prometheus-port`

TEI's HTTP variant serves `/metrics` on the main API port (3000 in this deploy). The `--prometheus-port` / `PROMETHEUS_PORT` setting does **not** open a separate listener — it's a no-op for the HTTP variant (likely applies only to the gRPC build). The HelmRelease originally set `PROMETHEUS_PORT: 9000` and the ServiceMonitor scraped `:9000`; the scrape silently failed with "connection refused" until corrected in PR #11898. Single port, single ServiceMonitor endpoint (`port: http`), CNP allows both Open WebUI and Prometheus on 3000.

### 4. Cold start is ~65 seconds, dominated by HF Hub model download

First-boot timing on Spark:

- Image pull: ~30 s (the TEI 121- image is large; CUDA libs + Rust binary).
- HF Hub artifact download (configs, tokenizer): ~1 s.
- Model weights (safetensors, ~570 MB) → PVC: ~48 s.
- CUDA backend load + warmup: ~14 s.
- HTTP server up + `Ready`: total ~65 s.

Subsequent pod restarts skip the HF Hub steps (PVC cache is warm) and complete in ~55 s. The `startupProbe` budget is 5 min (60 × 5 s) — comfortable margin, can be tightened to ~2 min if/when we want faster pod-failure detection.

### 5. DCGM utilization counters are broken on GB10

Per [reference_dcgm_gb10_broken_counters](../../reference_dcgm_gb10_broken_counters.md): `GPU_UTIL`, `MEM_COPY_UTIL`, `GR_ENGINE_ACTIVE`, `FB_USED` are stuck at 0 or empty on Spark. Only `POWER_USAGE` and `SM_CLOCK` report. Don't write GPU-utilization-based alerts; use request-success metrics instead.

## Configuration knobs

In `kubernetes/apps/ai/tei-spark/app/helmrelease.yaml`:

| Env | Value | Notes |
|---|---|---|
| `MODEL_ID` | `BAAI/bge-reranker-v2-m3` | Family-matched to bge-m3 embedder |
| `PORT` | `3000` | HTTP + metrics share this port |
| `HUGGINGFACE_HUB_CACHE` | `/data` | ceph-block PVC mount |
| `DTYPE` | `float16` | Halves VRAM use, sm_121 native |
| `AUTO_TRUNCATE` | `true` | Truncate over-long inputs rather than 413 |
| `JSON_OUTPUT` | `true` | Structured logs for Loki ingestion |

## Verification recipes

### Service is live and reranking

```sh
kubectl -n ai run --rm -i tei-smoke --image=curlimages/curl:8.10.1 --restart=Never \
  -- curl -sS -X POST http://tei-spark.ai.svc.cluster.local:3000/rerank \
  -H 'content-type: application/json' \
  -d '{"query":"What is RAG?",
       "texts":["RAG combines retrieval and generation.",
                "Pizza is delicious.",
                "Embeddings are vector representations."]}'
```

Expected: JSON array with `index 0` scored highest by an order of magnitude.

### Prometheus scrape is healthy

```sh
kubectl -n observability port-forward sts/prometheus-kube-prometheus-stack 19090:9090 &
curl -sS 'http://localhost:19090/api/v1/targets' \
  | jq '.data.activeTargets[] | select(.scrapeUrl | contains("tei-spark"))'
```

Expected: `"health": "up"`, scrapeUrl on port 3000.

### Alerts are loaded

`SparkTeiRerankPodDown` (critical, 5 min) and `SparkTeiRerankErrorRate` (warning, 10 min, >5%) ship in the same kustomization. Verify via Prom `/api/v1/rules` or the Alerts UI.

## Cutover history

| PR | Description |
|---|---|
| #11886 | PR-1 — scaffold HelmRelease (suspended) |
| #11893 | PR-2 — unsuspend; first pod up, smoke test against v2-m3 passes |
| #11898 | PR-2.1 — fix `/metrics` port wiring (was scraping wrong port) |
| #11906 | PR-4 — PrometheusRule (pod-down + error-rate alerts) |
| _pending_ | PR-3 — Open WebUI cutover to `RAG_RERANKING_ENGINE: external`; window-gated to Tue 2026-05-26 02:00 ET |

Before PR-3 lands, TEI is dark capacity — no consumer routes to it, alerts are inert. Soak metrics on the Spark side (POWER_USAGE baseline, pod memory) can still be observed during this window.

## Common failure modes (anticipated; populate as encountered)

- **HF Hub 429 during first start** — model weights download fails. Fix: delete pod, retry. Hub rate limits are per-IP and brief. Long-term mitigation: pre-stage weights into the PVC via a one-shot Job.
- **CUDA OOM** — bge-reranker-v2-m3 at FP16 needs ~1.5 Gi VRAM with batched requests; should never OOM on GB10's 128 Gi unified memory unless something else on the GPU is leaking. Check `nvidia-smi` on the Spark node.
- **Tokenizer panic on input** — TEI logs the offending input; not seen yet in this deploy. `AUTO_TRUNCATE: true` should prevent this for length issues.
