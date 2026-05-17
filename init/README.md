# init/

Shell scripts that orchestrate cluster bootstrap and teardown. Each script is meant to be run once at a specific phase of the cluster lifecycle. Nothing here is reconciled — these are one-shot operator tools.

Full end-to-end procedures live in [`docs/src/init_teardown.md`](../docs/src/init_teardown.md) and [`docs/src/cluster_rebuild.md`](../docs/src/cluster_rebuild.md). This README documents the individual scripts and the order they run in.

## Scripts

| Script | Run from | When | What it does |
|---|---|---|---|
| `kube-vip.sh` | `master1` (root) | Once, before `kubeadm init` | Renders the `kube-vip` static-pod manifest into `/etc/kubernetes/manifests/`. The VIP (`192.168.6.1`) is what `kubeadm init` will use as the control-plane endpoint. Interface defaults to `enp0s31f6`. |
| `clusterconfiguration.yaml` | `master1` (read by `create-cluster.sh`) | n/a (manifest) | The `ClusterConfiguration` + `InitConfiguration` `kubeadm` ingests. Sets the control-plane endpoint to the VIP, cri-socket to cri-o, and the bootstrap token. |
| `create-cluster.sh` | `master1` (root) | Once, after `kube-vip.sh` | `kubeadm init` with `clusterconfiguration.yaml`, joins masters 2/3 + every worker, labels Longhorn-eligible nodes, makes `master1` schedulable. Requires `SECRET_DOMAIN` env. |
| `initialize-cluster.sh` | Laptop | After `create-cluster.sh` finishes | Pulls kubeconfig from `master1`, runs `bootstrap/mod.just` recipes (1Password-templated Secrets → CRDs → bootstrap apps via helmfile). Ends when Flux is reconciling. |
| `approve-csrs.sh` | Laptop | Ad-hoc fallback | Approves pending node CSRs in bulk. Normally `kubelet-csr-approver` handles this automatically; this script is for the case where the auto-approver isn't up yet. |
| `destroy-cluster.sh` | Laptop | Tearing down to rebuild | Suspends Rook/Ceph + Longhorn HelmReleases, drains every node, runs `kubeadm reset`, wipes Ceph OSD devices, clears `/var/lib/{etcd,kubelet,longhorn,rook}`. **Destructive — only reuses the same hardware.** |

## Order

### First bring-up (or rebuild)

1. (`master1` only) Edit `init/kube-vip.sh` if the network interface or VIP differs from `enp0s31f6` / `192.168.6.1`.
2. On `master1`: `./init/kube-vip.sh`
3. On `master1`: `export SECRET_DOMAIN=...; ./init/create-cluster.sh`
4. On the laptop: `./init/initialize-cluster.sh`
5. On the laptop: `ssh root@master1 rm /etc/kubernetes/manifests/kube-vip.yaml` (the static pod is now redundant; Flux brings up the in-cluster kube-vip DaemonSet)

### Teardown (only if reusing the same hardware)

1. On the laptop: `./init/destroy-cluster.sh` — drains, resets, wipes
2. Verify NFS-backed bits (Garage substrate on `${NFS_HOST_0}`, Longhorn backup target on `beast`) are intact *before* running this. Lose those and CNPG recovery has no source. See [`docs/src/cluster_rebuild.md`](../docs/src/cluster_rebuild.md) → "Preflight".

## Prerequisites

Laptop:

- `kubectl`, `helmfile`, `helm`, `just`
- `op` (1Password CLI, signed in)
- SSH access to all cluster nodes as `root`

`master1`:

- A `kubeadm`-installed control plane (`kubeadm`, `kubelet`, `kubectl`, `cri-o`, `crun`)
- `podman` (used by `kube-vip.sh` to render the static-pod manifest)

## What lives here vs `bootstrap/`

- **`init/`** = shell scripts that *orchestrate* the bootstrap procedure (run kubeadm, scp kubeconfigs, etc.).
- **[`bootstrap/`](../bootstrap/)** = the resources that get *applied* during bootstrap (1Password-templated Secrets, CRDs, the bootstrap helmfile).

`initialize-cluster.sh` is the seam — it pulls kubeconfig and then drives the `bootstrap/mod.just` recipes.

## Related

- [`bootstrap/README.md`](../bootstrap/README.md) — what gets applied during bootstrap.
- [`docs/src/init_teardown.md`](../docs/src/init_teardown.md) — minimal procedure.
- [`docs/src/cluster_rebuild.md`](../docs/src/cluster_rebuild.md) — full bootstrap + CNPG recovery walkthrough.
- [`docs/src/promote_worker_to_control_plane.md`](../docs/src/promote_worker_to_control_plane.md) — for the case where you're swapping a control-plane node, not full bring-up.
- [`docs/src/power-outage.md`](../docs/src/power-outage.md) — for the case where the cluster cold-started and `kube-vip` is racing the apiserver.
