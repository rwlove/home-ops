# tools/

Operational helper scripts for the home-ops cluster. Most assume `kubectl`,
`flux`, and `ssh root@<node>` access. None of them are part of the GitOps
flow — they're for ad-hoc operator work.

**Convention:** a *one-shot* `kubectl`/`flux` incantation belongs as a `just`
recipe (`just k8s …`), not a new standalone script here — recipes are
discoverable via `just --list` and can't rot into dead doc links. Reserve
`tools/` for genuinely multi-step scripts. Cluster lifecycle is `just cluster …`
(see [`init/README.md`](../init/README.md)); bootstrap is `just bootstrap …`.

## Cluster lifecycle / bootstrap

| Script | Purpose |
|---|---|
| `check-kubeadm-config-drift.sh` | Advisory diff of `init/clusterconfiguration.yaml` (what a rebuild installs) vs the live `kubeadm-config` ConfigMap. Run before any rebuild. |

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
| `clear-stuck-cni-sandbox.sh` | Force-remove a cri-o sandbox stuck on a missing CNI plugin. |

## GPU / NVIDIA

| Script | Purpose |
|---|---|
| `list_pods_on_nvidia_runtimeclass.sh` | List pods using `runtimeClassName: nvidia`. |

## Cilium / etcd

| Script | Purpose |
|---|---|
| `etcd-defrag.sh` | Defrag the etcd cluster (fixes the `EtcdDatabaseHighFragmentationRatio` alert). |

## Cleanup

| Script | Purpose |
|---|---|
| `remove-failed-storage-jobs.sh` | Delete Failed Jobs in the `storage` namespace. |
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
| `lint-flux-ks.py` | Require explicit `wait` and `timeout` on every child Flux Kustomization, and reject child `sourceRef` / `retryInterval` / `prune: true` (the tier parents' patch injects and overrides all three). See `.agents/instructions/flux.dependencies.md`. Wired into `.pre-commit-config.yaml` and the `Lint` GitHub Actions workflow. |
| `lint-flux-parents.py` | Require every Flux tier parent in `kubernetes/flux/cluster/ks.yaml` to carry the `home-ops.io/tier-parent` label and the same spec apart from `path`/`prune`, with Orphan, both substitution sources, sourceRef, dependsOn cluster-config and the child-default patches; the parent paths equal the roots one to one; the expected three child-default patches including the `child-prune=disabled` opt-out; `prune: false` only with a `home-ops.io/move-window` annotation; and no other YAML (or root-level patches) in that dir. The parents are inline copies because flate never applies kustomize Component patches. Wired into `.pre-commit-config.yaml` and the `Lint` workflow. |
| `tier-move-gates.sh` | Read-only gates for a move between tier parents (baseline, render union, inventory/owner/leak/generation checks, GC watch, the `prune: true` precondition, post-merge CI). Parents and roots are read from `kubernetes/flux/cluster/ks.yaml`. Steps in `docs/src/gitops_layout.md`; also `just k8s tier-gate <mode>`. |
| `lint-tier-membership.py` | Require every namespace dir under `kubernetes/apps/` to be listed by exactly one Flux parent root (`kubernetes/apps/kustomization.yaml` or `kubernetes/tiers/*/kustomization.yaml`), or commented out in exactly one; roots may list only whole namespace dirs. An unlisted dir is silently unmanaged; a doubly-listed one makes two parents fight over its owner labels. Wired into `.pre-commit-config.yaml` and the `Lint` workflow. |
| `test-prometheusrules.sh` | Run promtool unit tests for every `*prometheusrule.test.yaml` under `kubernetes/`: extracts `.spec` from the sibling PrometheusRule, then `promtool check rules` + `promtool test rules` in the pinned Prometheus image (docker or podman, needs yq). Adding a test needs no CI edit. Run by the `Promtool` GitHub Actions workflow (`Rule tests` job, only when a `*prometheusrule*.yaml` changes). |
| `lint-envsubst-shell-vars.py` | Reject container commands with unescaped shell vars that Flux's `envsubst` post-build would clobber. Wired into `.pre-commit-config.yaml` and the `Lint` workflow. |
| `lint-esphome-config-substitutions.py` | Reject unescaped ESPHome `${var}` substitutions in the git-tracked device configs under `kubernetes/apps/home/esphome/app/resources/` — Flux's `envsubst` post-build runs strict-mode over the `esphome-devices` ConfigMap and wedges the Kustomization (bit the repo in #15068/#15070, fixed #15071). Wired into `.pre-commit-config.yaml` and the `Lint` workflow. |
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
| `immich-frame-video-transcode.sh` | Transcode a video for an Immich frame (reads the Immich API key from 1Password). |

## Subdirectories

| Path | Contents |
|---|---|
| `cluster-upgrade/` | Control-plane / node OS upgrade helpers. |
| `os-cve-scan/` | Host-OS CVE scanning helpers. |
| `git-hooks/` | Repo git hooks (e.g. `pre-commit`). |
| `data/` | Static data used by other tools. |

## One-liner operations (retired scripts)

These were previously stand-alone scripts; recorded here as `kubectl`/`flux`/`ssh` one-liners so the history is grep-able without the file noise.

| Operation | Command |
|---|---|
| Force-reconcile a tier parent | `flux reconcile source git home-ops-kubernetes -n flux-system`, then `flux reconcile kustomization cluster-<infra\|platform\|apps\|media> -n flux-system` (see `docs/src/gitops_layout.md`) |
| Print Ceph dashboard password | `just k8s ceph-password` |
| Spawn netshoot | `just k8s netshoot` (or `kubectl -n downloads run tmp-shell --rm -i --tty --image nicolaka/netshoot`) |
| Run nvidia-smi on a GPU node | `kubectl -n default run nvidia-shell -i --tty --overrides='{"apiVersion":"v1","spec":{"nodeSelector":{"nvidia.com/gpu.present":"true"},"runtimeClassName":"nvidia"}}' --image nvidia/cuda:12.6.2-devel-ubuntu22.04 -- nvidia-smi` |
| nvtop on worker8 | `ssh -t root@worker8 nvtop` |
| One-time CNPG backup | `just k8s pg-backup-now <app>` |
