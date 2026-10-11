# Initialization & Teardown

The bare-command set to bring the cluster up from nothing, or tear it down to nothing. See [Cluster Rebuild](cluster_rebuild.md) for the full preflight and verification procedure that wraps these commands.

## Prerequisites (laptop)

- `kubectl` — cluster access
- `just` — runs the `bootstrap/mod.just` recipes (this repo uses `just`, not `go-task`)
- `helmfile` + `helm` — apply the bootstrap CRDs and apps
- `op` (1Password CLI) — for rendering `bootstrap/resources.yaml.j2`
- `minijinja-cli` — template renderer used by the bootstrap step
- `yq` — YAML processing

Install via `dnf` / `brew` / your package manager of choice. The scripts do not pre-validate these — a missing tool surfaces as a command-not-found failure mid-run.

## Initialization

All from the **laptop** — the repo no longer needs a checkout on `master1`; `create-cluster.sh` orchestrates the nodes over `ssh`.

1. **Bring up the cluster end-to-end:**

   ```sh
   export SECRET_DOMAIN=<your-cluster-domain>
   just cluster create
   ```

   Renders kube-vip on `master1`, runs `kubeadm init`, pulls the kubeconfig, joins masters 2/3 and all workers, labels Longhorn nodes, makes `master1` schedulable, then bootstraps the in-cluster apps (1Password-backed secrets → CRDs from `00-crds.yaml` → `helmfile sync` of `01-apps.yaml`: Cilium, CoreDNS, cert-manager, external-secrets, 1Password Connect, Flux operator + instance).

2. **Remove the redundant static kube-vip manifest** once Flux reconciles the in-cluster kube-vip DaemonSet (a few minutes — the script prints this reminder):

   ```sh
   ssh root@master1 rm /etc/kubernetes/manifests/kube-vip.yaml
   ```

   The static pod is redundant once the DaemonSet owns the VIP, and will otherwise fight for it.

## Teardown

Wipes the cluster, destroys Ceph OSDs, clears `/var/lib/{etcd,kubelet,longhorn,rook}`. Only run when reusing the same hardware — fresh hardware doesn't need this.

```sh
just cluster destroy
```

**This is destructive.** See [Cluster Rebuild](cluster_rebuild.md) → "Preflight" for the survival audit you should run *before* destroying anything. The Garage S3 buckets (CNPG backups) and any NFS-backed data are the only things that survive teardown — verify the NFS host is healthy first.

## Related

- [Cluster Rebuild](cluster_rebuild.md) — full end-to-end recovery including post-bootstrap verification and CNPG recovery from Garage.
