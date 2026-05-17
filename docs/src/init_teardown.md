# Initialization & Teardown

The bare-command set to bring the cluster up from nothing, or tear it down to nothing. See [Cluster Rebuild](cluster_rebuild.md) for the full preflight and verification procedure that wraps these commands.

## Prerequisites (laptop)

- `op` (1Password CLI) — for rendering `bootstrap/resources.yaml.j2`
- `minijinja-cli` — template renderer used by the bootstrap step
- `yq` — YAML processing
- `go-task` (alias `task`) — runs the `bootstrap/mod.just` recipes

Install via `dnf` / `brew` / your package manager of choice. The bootstrap scripts will exit early if any of these are missing.

## Initialization

In order:

1. **On `master1`** (control-plane bootstrap):

   ```sh
   ./init/create-cluster.sh
   ```

   Sets up kube-vip, runs `kubeadm init`, joins masters 2/3 and all workers, labels Longhorn-eligible nodes, makes `master1` schedulable.

2. **On the laptop** (in-cluster app bootstrap):

   ```sh
   ./init/initialize-cluster.sh
   ```

   Pulls the kubeconfig from `master1`, creates bootstrap namespaces, renders 1Password-backed secrets, applies CRDs from `bootstrap/helmfile.d/00-crds.yaml`, then runs `helmfile sync` for `01-apps.yaml` (Cilium, CoreDNS, cert-manager, external-secrets, 1Password Connect, Flux operator + instance).

3. **On the laptop** (remove redundant static manifest):

   ```sh
   ssh root@master1 rm /etc/kubernetes/manifests/kube-vip.yaml
   ```

   Once Flux brings up the in-cluster kube-vip, the static pod is redundant and will fight for the VIP. Remove it after step 2 settles.

## Teardown

Wipes the cluster, destroys Ceph OSDs, clears `/var/lib/{etcd,kubelet,longhorn,rook}`. Only run when reusing the same hardware — fresh hardware doesn't need this.

```sh
./init/destroy-cluster.sh
```

**This is destructive.** See [Cluster Rebuild](cluster_rebuild.md) → "Preflight" for the survival audit you should run *before* destroying anything. The Garage S3 buckets (CNPG backups) and any NFS-backed data are the only things that survive teardown — verify the NFS host is healthy first.

## Related

- [Cluster Rebuild](cluster_rebuild.md) — full end-to-end recovery including post-bootstrap verification and CNPG recovery from Garage.
