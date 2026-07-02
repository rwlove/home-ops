# tools/

Operational helper scripts for the home-ops cluster. Most assume `kubectl`,
`flux`, and `ssh root@<node>` access. None of them are part of the GitOps
flow — they're for ad-hoc operator work.

## Cluster-wide

| Script | Purpose |
|---|---|
| `run-on-all-nodes.sh` | Run a command via `ssh root@<node>` on every node (discovered via kubectl). |
| `events.sh` | Watch cluster events with the chatty Flux ones filtered out. Env vars: `NAMESPACE`, `WARNINGS_ONLY=1`, `SHOW_FILTERED=1` (audit), `EVENTS_FILTER_EXTRA`. |
| `get_unfulfilled_deployments.sh` | List Deployments / ReplicaSets / StatefulSets whose ready < desired. |

## CNPG Postgres

| Script | Purpose |
|---|---|
| `check-postgres-dbs.sh` | Show Status / Instances / Ready instances for every CNPG cluster. |
| `check-cnpg-soak.sh` | Post-rollout soak check (restarts, OOMs, memory headroom). |
| `kill-postgres-pod.sh` | Wipe a CNPG replica's PV+PVC+pod so cnpg re-clones from primary. |

## Rook / Ceph

| Script | Purpose |
|---|---|
| `destroy-rook-ceph-cluster.sh` | Catastrophic: tear down the entire Ceph cluster. |

## NFS

| Script | Purpose |
|---|---|
| `replace-and-apply-pvc.sh` | `envsubst` NFS host placeholders into a PVC file then apply. |
| `stop-NFS_HOST_0-volumes.sh` | Scale workloads off NFS volumes served by `brain.${SECRET_DOMAIN}`. |

## Networking & debugging

| Script | Purpose |
|---|---|
| `check_jellyfin-internal.sh` | curl the internal jellyfin hostname. |
| `check_k8s-gateway.sh` | nslookup against the in-cluster gateway DNS. |
| `check_smtp-relay.sh` | Send a test email through smtp-relay. |
| `clear-stuck-cni-sandbox.sh` | Force-remove a cri-o sandbox stuck on a missing CNI plugin. |

## GPU / NVIDIA

| Script | Purpose |
|---|---|
| `list_pods_on_nvidia_runtimeclass.sh` | List pods using `runtimeClassName: nvidia`. |

## Cilium / etcd

| Script | Purpose |
|---|---|
| `cilium-install-cli.sh` | Install the upstream Cilium CLI binary. |
| `etcd-defrag.sh` | Defrag the etcd cluster (fixes the `EtcdDatabaseHighFragmentationRatio` alert). |

## Cleanup

| Script | Purpose |
|---|---|
| `remove-failed-storage-jobs.sh` | Delete Failed Jobs in the `storage` namespace. |
| `remove-old-replicasets.sh` | Delete ReplicaSets with `replicas=0`. |
| `restart-k8s-service.sh` | Scale a Deployment to 0, wait, scale back up. |
| `enable-disable-hr.sh` | Pause/unpause a HelmRelease via the `disable-<app>` commit pattern. |

## Auth + registry helpers

| Script | Purpose |
|---|---|
| `gen-oauth-client-secret.sh` | Generate an Authelia OAuth2 client secret + matching argon2 hash (paste both into `clients.yaml` / 1Password). |
| `check-image-registry.sh` | Verify image registries used in the repo against the allowlist (renovate / PR-review aid). |

## Lint + pre-commit hooks

| Script | Purpose |
|---|---|
| `lint-cnp-empty-rules.py` | Reject CiliumNetworkPolicy manifests that select endpoints but define no ingress/egress rules — Cilium 1.19 silently fails to apply them. Wired into `.pre-commit-config.yaml` and the `Lint` GitHub Actions workflow. |
| `lint-readme-drift.py` | Verify that `README.md` badge values match live cluster data. Wired into `.pre-commit-config.yaml`. |
| `check-readme-drift-vs-main.sh` | CI helper that compares README badge values against `origin/main`. Wired into `.pre-commit-config.yaml`. |

## Claude Code operator

> TODO: consider relocating these to `~/.claude-personal/scripts/` — they are Claude Code session helpers, not cluster ops tools, and don't belong alongside `destroy-rook-ceph-cluster.sh`.

| Script | Purpose |
|---|---|
| `claude-worktree.sh` | Create a dated git worktree (`home-ops.worktrees/YYYY-MM-DD-<slug>`) on a new `claude/<…>` branch off `origin/main`. |
| `claude-worktree-cleanup.sh` | Remove merged Claude worktrees and their branches. |

## App-specific

| Script | Purpose |
|---|---|
| `frigate_copy_speed.sh` | Tail Frigate logs and print recording copy throughput. |
| `ollama-pull-models.sh` | Pull a predefined set of models into the Ollama instance. |
| `romm-apply-tags.sh` | Apply per-ROM tags in the RomM database after a scan. |

## One-liner operations (retired scripts)

These were previously stand-alone scripts; recorded here as `kubectl`/`flux`/`ssh` one-liners so the history is grep-able without the file noise.

| Operation | Command |
|---|---|
| Force-reconcile cluster-apps | `flux --namespace flux-system reconcile kustomization cluster-apps --with-source` |
| Print Ceph dashboard password | `kubectl -n rook-ceph get secret rook-ceph-dashboard-password -o jsonpath="{['data']['password']}" \| base64 --decode && echo` |
| Spawn netshoot in downloads ns | `kubectl -n downloads run tmp-shell --rm -i --tty --image nicolaka/netshoot` |
| Run nvidia-smi on a GPU node | `kubectl -n default run nvidia-shell -i --tty --overrides='{"apiVersion":"v1","spec":{"nodeSelector":{"nvidia.com/gpu.present":"true"},"runtimeClassName":"nvidia"}}' --image nvidia/cuda:12.6.2-devel-ubuntu22.04 -- nvidia-smi` |
| nvtop on worker8 | `ssh -t root@worker8 nvtop` |
| Apply one-time CNPG backup | `kubectl apply -f kubernetes/apps/databases/cloudnative-pg/config/onetimebackup.yaml` |
