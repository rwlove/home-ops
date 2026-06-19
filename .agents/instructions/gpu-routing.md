# GPU routing

This file is a pointer plus a small set of home-ops-local facts.
Model-to-GPU routing decisions don't live here.

## Canonical source

The cross-repo authority lives in
`~/workspace/claude-workspace/langgraph-agents/.agents/instructions/hardware-routing.md`.
Read that before scheduling model work in this cluster.

## Local cluster GPU inventory

- **P40** (Pascal, 24GB VRAM) — currently on a worker node. Used for
  ≤8b-class inference; bge-m3 + nomic embedders; Ollama-served.
  Pre-Spark generation.
- **DGX Spark** (NVIDIA GB10, Grace-Blackwell) — on its own host
  running Ubuntu 24.04 / containerd (the lone non-CRI-O node — see
  `reference_cluster_runtime_inventory` in memory). Used for larger
  inference; the Spark migration is in progress and specific model
  assignments live in `hardware-routing.md` upstream.

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

- Most DCGM counters are broken on GB10 — `GPU_UTIL` and
  `MEM_COPY_UTIL` stuck at 0; `GR_ENGINE_ACTIVE` and `FB_USED` empty.
- Only `POWER_USAGE` and `SM_CLOCK` report.
- Use power draw as proxy for "is GB10 busy" in dashboards and alerts.

## Routing decisions

- Defer to `hardware-routing.md` in langgraph-agents — don't replicate
  model-to-GPU mappings here.
- Local rule of thumb: ≤8b → P40; larger → Spark (until the Spark
  migration is documented as complete).

## What this is NOT

- Not the canonical GPU routing doc — that's in langgraph-agents.
- Not a snapshot of every node's hardware — `kubectl get nodes -o yaml
  | grep nvidia` is the source of truth.
