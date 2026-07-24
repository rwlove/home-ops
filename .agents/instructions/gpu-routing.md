# GPU routing

This file is the source of truth for model-to-GPU routing in this
cluster. It used to defer to a canonical doc in the `langgraph-agents`
repo (`hardware-routing.md`), but that doc described langgraph-agents'
own internal per-agent routing factory — machinery that no longer
exists (the fleet was decommissioned 2026-07-06, no value delivered).
There was no cluster-wide routing content there beyond what's already
below; nothing was migrated because there was nothing left to migrate.

## Local cluster GPU inventory

- **P40** (Pascal, 24GB VRAM) — currently on a worker node. Used for
  ≤8b-class inference; bge-m3 + nomic embedders; Ollama-served.
  Pre-Spark generation.
- **DGX Spark** (NVIDIA GB10, Grace-Blackwell) — on its own host
  running Ubuntu 24.04 / containerd (the lone non-CRI-O node — see
  `reference_cluster_runtime_inventory` in memory). Used for larger
  inference; the Spark migration is in progress.

## Runtime split matters for gpu-operator

- The cluster is mostly CRI-O on CentOS Stream 9; Spark is the lone
  containerd outlier.
- gpu-operator's `container-toolkit` DaemonSet is containerd-only.
- PR #11760 installed a `NodeFeatureRule` that auto-labels CRI-O nodes
  (`OS ID = centos/fedora/rhel`) with
  `nvidia.com/gpu.deploy.container-toolkit=false`, skipping the
  toolkit DS on those nodes.
- When the OS migration completes (everything on Ubuntu/containerd),
  the NFD rule flips off naturally — no manual cleanup.

## DCGM counters on GB10

Re-verified live 2026-07-24 (driver 580.142). The picture improved
since the original May observation — `GPU_UTIL` now works:

- **Working:** `GPU_UTIL` (tracks load — read 36→96 under comfyui load),
  `GPU_TEMP`, `POWER_USAGE`, `SM_CLOCK`, `TOTAL_ENERGY_CONSUMPTION`.
  Prefer `GPU_UTIL` for "is GB10 busy" now; `POWER_USAGE` is a fine
  secondary (no longer the *only* proxy).
- **Still dead:** `MEM_COPY_UTIL` and `MEMORY_TEMP` stuck at 0;
  `GR_ENGINE_ACTIVE` and all `FB_*` (memory) fields **absent** (field
  not registered).
- **GPU memory (`FB_USED`) is permanently unavailable** — unified
  LPDDR5X, no discrete framebuffer, so `nvmlDeviceGetMemoryInfo`
  returns `NOT_SUPPORTED`. NVIDIA states "no plans to support DCGM on
  Spark." **No firmware/BIOS/driver fix exists.** Read GPU memory from
  `/proc/meminfo` on the Spark host (`kubectl top` undercounts CUDA on
  GB10). The `dcgmi dmon -e 1001` workaround for `GR_ENGINE_ACTIVE`
  (dcgm-exporter#662) is **not runnable** here — the exporter build is
  distroless with no `dcgmi`, and the field isn't registered on GB10.
- The fix for `GPU_UTIL` landed with the driver string unchanged
  (580.142), so it came from the DCGM-exporter/gpu-operator side or
  firmware, not the driver.

## GB10 power-delivery / clock-limit gotcha

Community-reported (NVIDIA DGX Spark GB10 forum + r/LocalLLM), not yet
independently reproduced on our unit — treat as a lead, not gospel:

- There's a **power-delivery bug** that sneakily degrades throughput.
  A reboot does **not** clear it; check the NVIDIA dev forum GB10
  category if the Spark feels slow with no obvious cause. Pairs with
  the DCGM caveat above — `POWER_USAGE` is a live counter, watch it
  for anomalies.
- Two operational levers users run to keep it stable:

  ```sh
  # drop page-cache remnants periodically
  sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'
  # cap GPU clocks if you hit crashes under load
  sudo nvidia-smi -lgc 200,2300
  ```

  The clock cap trades a little peak throughput for stability; only
  reach for it if the Spark is crashing under inference load.

## Community model picks for a single GB10 (reference)

External signal from r/LocalLLM DGX Spark owners — anchors for what
"larger → Spark" means in practice. Not a mandate; we haven't
benchmarked these on our unit:

- **Qwen 3.5 122B-A10B** — the common single-Spark top pick; users
  report comfortable 192k-token context.
- **Qwen 3.6 27B (with MTP) / 35B-A3B** — faster MoE option; the
  35B-A3B reportedly follows the Claude/opencode/codex agentic-coding
  loop better.
- **MiniMax M2.7 AWQ 4-bit** — favored on 2× Spark clusters (~25 tok/s
  on a 110k-token session, ~2K tok/s prompt-processing); too big for a
  single node.
- Utility models people co-locate until capacity-bound: **bge-m3**
  (embeddings, which we already run), **Parakeet** / **Qwen TTS**
  (speech).
- Serving consensus is **vLLM over Ollama** for the Spark (headless
  inference server), vs the P40's current Ollama serving.

Benchmarks aggregator: <https://spark-arena.com>.

## Routing decisions

- Rule of thumb: ≤8b → P40; larger → Spark (until the Spark migration
  is documented as complete).

## What this is NOT

- Not a snapshot of every node's hardware — `kubectl get nodes -o yaml
  | grep nvidia` is the source of truth.
