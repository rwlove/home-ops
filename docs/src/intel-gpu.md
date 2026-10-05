# Intel iGPU (Quick Sync transcode)

Intel HD Graphics 630 integrated GPUs on the i7-7700 worker nodes, exposed to
the cluster as `gpu.intel.com/i915` and used for Quick Sync (QSV) hardware
transcode.

## Overview

Several i7-7700 nodes (Lenovo M910x Tiny class) carry an Intel **HD Graphics
630** iGPU (PCI device `8086:5912`, class `0300`, at `00:02.0`). These are
exposed to pods as `gpu.intel.com/i915` resources, each GPU shared 3 ways
(`sharedDevNum: 3`), and consumed for hardware transcode by **Jellyfin** and
**Frigate**.

Dedicated transcode nodes are the bare-metal workers (`worker2`, `worker3`,
`worker4`). `master1` is a schedulable control-plane node that carries the same
iGPU; it advertises `i915` but is not a preferred transcode target (keep heavy
QSV load off the etcd/apiserver node).

## Hardware

| Field | Value |
| --- | --- |
| GPU | Intel HD Graphics 630 (Kaby Lake GT2) |
| PCI ID | `8086:5912` (class `0300`, bus `00:02.0`) |
| Host CPU | Intel i7-7700 |
| Cluster resource | `gpu.intel.com/i915` (3× shared per GPU) |
| Consumers | Jellyfin, Frigate (QSV / VAAPI transcode) |

## el10 node build — disable the `simpledrm` ghost card (REQUIRED)

> **Applies to any Intel-iGPU node on el10 / kernel 6.12+.** el9 nodes
> (kernel 5.14) do **not** need this — i915 claims `card0` directly there.

On el10 the EFI framebuffer handoff driver `simpledrm` grabs the iGPU as a
DRM card (`card0`) early in boot; i915 then claims the real card (`card1`) and
removes simpledrm's device, but udev leaves a **dangling**
`/dev/dri/by-path/...-simple-framebuffer.0-card` symlink pointing at the
now-gone `card0`. The Intel GPU device-plugin enumerates every `*-card`
by-path entry and bakes them into the container device spec, so the kubelet
tries to bind the dead symlink and **every `gpu.intel.com/i915` pod on that
node fails with `CreateContainerError`** (`cannot stat … No such file or
directory`). The GPU itself is fine (`i915` + `renderD128` are present) — only
the stale symlink breaks container creation.

Fix: stop `simpledrm` from ever registering the ghost card. When building /
upgrading an Intel-iGPU node to el10, add the kernel argument:

```sh
grubby --update-kernel=ALL --args="initcall_blacklist=simpledrm_platform_driver_init"
# verify it also landed in the el10 BLS persistent source (survives kernel updates):
grep initcall_blacklist /etc/kernel/cmdline
```

Reboot (drain first — see [Cluster Rebuild](cluster_rebuild.md) / the node
maintenance flow). After reboot, `simpledrm` never initializes, i915 becomes
`card0`, `/dev/dri/by-path` has no dangling entry, and the node's device
topology is identical to the el9 workers. `initcall_blacklist` is a safe no-op
if the symbol is ever wrong, so it's harmless to pre-apply.

Verify post-reboot:

```sh
ls /dev/dri/                                   # card0 + renderD128 (no card1, no ghost)
find /dev/dri/by-path -xtype l                 # empty — no dangling symlinks
lspci -k -s 00:02.0 | grep 'driver in use'     # i915
```

This mirrors the host-level `grubby` pattern used for the [P40](p40.md); node
kernel cmdline is host-managed, not GitOps.

## Cluster integration

| Component | Source |
| --- | --- |
| NFD label `intel.feature.node.kubernetes.io/gpu` | [`node-feature-discovery/rules/intel-gpu.yaml`](https://github.com/rwlove/home-ops/blob/main/kubernetes/apps/kube-system/node-feature-discovery/rules/intel-gpu.yaml) — matches Intel display-class PCI devices |
| Device plugin (`gpu.intel.com/i915`, `sharedDevNum: 3`) | [`intel-device-plugin/gpu/helmrelease.yaml`](https://github.com/rwlove/home-ops/blob/main/kubernetes/apps/kube-system/intel-device-plugin/gpu/helmrelease.yaml) |
| Metrics exporter (`ServiceMonitor`) | [`intel-device-plugin/exporter/helmrelease.yaml`](https://github.com/rwlove/home-ops/blob/main/kubernetes/apps/kube-system/intel-device-plugin/exporter/helmrelease.yaml) |

The NFD rule is a pure hardware-presence match (Intel vendor + display class), so
it correctly labels any node with an Intel iGPU — it is **not** the place to fix
the el10 ghost card. The ghost is a driver/boot artifact; fix it at the kernel
cmdline as above.

### Workload-side request

```yaml
resources:
  limits:
    gpu.intel.com/i915: 1
```
