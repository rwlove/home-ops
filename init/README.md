# init/

Shell scripts that orchestrate cluster bootstrap and teardown. Each script is meant to be run once at a specific phase of the cluster lifecycle. Nothing here is reconciled — these are one-shot operator tools.

Full end-to-end procedures live in [`docs/src/init_teardown.md`](../docs/src/init_teardown.md) and [`docs/src/cluster_rebuild.md`](../docs/src/cluster_rebuild.md). This README documents the individual scripts and the order they run in.

## Scripts

Everything is driven from the **laptop** now — the repo no longer needs a checkout on `master1`. `create-cluster.sh` orchestrates `master1` (and the other nodes) over `ssh`.

| Script | Run from | When | What it does |
|---|---|---|---|
| `nodes.sh` | n/a (sourced) | n/a | Canonical node inventory (`MASTER_INIT`, `JOIN_MASTERS`, `WORKERS`, `LONGHORN_NODES`, teardown order). Sourced by create/destroy. |
| `clusterconfiguration.yaml` | n/a (manifest) | n/a | The `ClusterConfiguration` + `InitConfiguration` `kubeadm` ingests. `create-cluster.sh` renders it (`envsubst`) and `scp`s it to `master1`. |
| `create-cluster.sh` | Laptop (`just cluster create`) | Once, full bring-up | End-to-end: renders kube-vip on `master1`, `kubeadm init`, pulls the kubeconfig, joins masters 2/3 + every worker, labels Longhorn nodes, makes `master1` schedulable, then bootstraps the in-cluster apps (secrets → CRDs → bootstrap helmfile → Flux). Requires `SECRET_DOMAIN`. |
| `kube-vip.sh` | A control-plane host (root) | Standalone | The canonical kube-vip static-pod generator, used by [`promote_worker_to_control_plane.md`](../docs/src/promote_worker_to_control_plane.md). `create-cluster.sh` inlines an equivalent render over `ssh`; keep VIP/interface/version in sync. |
| `approve-csrs.sh` | Laptop (`just cluster approve-csrs`) | Ad-hoc fallback | Approves pending node CSRs in bulk when `kubelet-csr-approver` isn't up yet. |
| `destroy-cluster.sh` | Laptop (`just cluster destroy`) | Tearing down to rebuild | Prompts for a `DESTROY` confirmation, suspends the Rook/Ceph HelmReleases, drains every node, runs `kubeadm reset`, wipes Ceph OSD devices, clears `/var/lib/{etcd,kubelet,longhorn,rook}`. **Destructive — only reuses the same hardware.** |

## Order

### First bring-up (or rebuild)

1. (Optional) Edit the kube-vip constants (`VIP` / `VIP_INTERFACE`) in `init/create-cluster.sh` if they differ from `192.168.6.1` / `enp0s31f6`.
2. On the laptop: `export SECRET_DOMAIN=...; just cluster create` (or `./init/create-cluster.sh`).
3. On the laptop, once Flux reconciles the in-cluster kube-vip DaemonSet (a few minutes): `ssh root@master1 rm /etc/kubernetes/manifests/kube-vip.yaml` (the static pod is now redundant and will fight for the VIP). The script prints this reminder on completion.

### Teardown (only if reusing the same hardware)

1. On the laptop: `just cluster destroy` — drains, resets, wipes (prompts to confirm).
2. Verify NFS-backed bits (Garage substrate on `${NFS_HOST_0}`, Longhorn backup target on `beast`) are intact *before* running this. Lose those and CNPG recovery has no source. See [`docs/src/cluster_rebuild.md`](../docs/src/cluster_rebuild.md) → "Preflight".

## Prerequisites

Laptop (everything runs here):

- `kubectl`, `just`, `helmfile`, `helm`
- `op` (1Password CLI, signed in), `minijinja-cli`, `yq`, `envsubst`
- SSH access to all cluster nodes as `root`

`master1` and the other nodes:

- A `kubeadm`-ready control plane host (`kubeadm`, `kubelet`, `cri-o`, `crun`)
- `podman` on `master1` (used to render the kube-vip static-pod manifest)

## What lives here vs `bootstrap/`

- **`init/`** = shell scripts (and the `just cluster` recipes) that *orchestrate* the procedure (run kubeadm over ssh, scp kubeconfigs, etc.).
- **[`bootstrap/`](../bootstrap/)** = the resources that get *applied* during bootstrap (1Password-templated Secrets, CRDs, the bootstrap helmfile), driven by `just bootstrap resources|crds|apps`.

`create-cluster.sh`'s final phase is the seam — it pulls the kubeconfig and then drives the `just bootstrap …` recipes.

## Related

- [`bootstrap/README.md`](../bootstrap/README.md) — what gets applied during bootstrap.
- [`docs/src/init_teardown.md`](../docs/src/init_teardown.md) — minimal procedure.
- [`docs/src/cluster_rebuild.md`](../docs/src/cluster_rebuild.md) — full bootstrap + CNPG recovery walkthrough.
- [`docs/src/promote_worker_to_control_plane.md`](../docs/src/promote_worker_to_control_plane.md) — for the case where you're swapping a control-plane node, not full bring-up.
- [`docs/src/power-outage.md`](../docs/src/power-outage.md) — for the case where the cluster cold-started and `kube-vip` is racing the apiserver.
