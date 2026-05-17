# Resources: Limits and Requests Philosophy

Cluster-wide convention for `resources.requests` and `resources.limits` on every workload.

## TL;DR

- **`resources.requests.cpu`**: set it (used by the scheduler).
- **`resources.limits.cpu`**: don't set it.
- **`resources.requests.memory`**: set it.
- **`resources.limits.memory`**: set it equal to the request.

```yaml
resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    memory: 256Mi
```

## Why

### CPU: request without limit

CPU is **compressible** — when a node is under pressure, the kernel time-slices and everybody gets less. Setting a CPU limit caps the workload at that ceiling even when the node has idle headroom, which trades a fictional "guarantee" for real-world throttling.

`requests.cpu` is what the scheduler uses for bin-packing decisions; that's the number that matters. The limit is mostly downside.

Reference: [Stop using CPU limits — Robusta](https://home.robusta.dev/blog/stop-using-cpu-limits).

### Memory: request == limit

Memory is **incompressible** — once allocated, the kernel can't claw it back. The kernel resolves memory pressure via the OOM killer, which evicts based on QoS class.

Setting `requests.memory == limits.memory` puts the pod in the **`Guaranteed`** QoS class — last to be OOM-killed during node pressure. Splitting them (e.g. request 256 Mi, limit 1 Gi) puts the pod in **`Burstable`**, which the scheduler can pack more densely but which is OOM-killed sooner during contention.

For predictable workloads (most of this cluster), `Guaranteed` beats `Burstable`. Pick a `memory` value that's "what the app actually uses + headroom" and use it for both.

Reference: [Kubernetes memory limits — Robusta](https://home.robusta.dev/blog/kubernetes-memory-limit).

## Tools in this cluster

- **Goldilocks** (in `observability/`) — VPA-driven right-sizing recommendations. Cross-check against your hand-picked values periodically.
- **kube-prometheus-stack** — `kube_pod_container_resource_requests` / `_limits` metrics; `container_memory_working_set_bytes` for actual usage.
- **`kubectl top pods -A --sort-by=memory`** for a quick top-N look.

## Exceptions

Two patterns where the rule above doesn't apply cleanly:

- **GPU workloads** under `nvidia.com/gpu` are governed by the time-slicing config, not CPU/memory ratios. See [`p40.md`](p40.md).
- **Workloads with known memory leaks** (e.g. Frigate's ffmpeg leak — see project memory) need a memory limit that's higher than steady-state to provide headroom before OOMKilled restarts kick in.

When the exception is permanent, leave a comment in the manifest noting *why* the rule is being broken.
