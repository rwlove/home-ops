# tools/

Operational helper scripts for the home-ops cluster. Most assume `kubectl`,
`flux`, and `ssh root@<node>` access. None of them are part of the GitOps
flow — they're for ad-hoc operator work.

## Cluster-wide

| Script | Purpose |
|---|---|
| `run-on-all-nodes.sh` | Run a command via `ssh root@<node>` on every node (discovered via kubectl). |
| `reconcile-cluster.sh` | Force-reconcile the `cluster-apps` Flux Kustomization. |
| `events.sh` | Watch cluster events with the chatty Flux ones filtered out. Env vars: `NAMESPACE`, `WARNINGS_ONLY=1`, `SHOW_FILTERED=1` (audit), `EVENTS_FILTER_EXTRA`. |
| `get_unfulfilled_deployments.sh` | List Deployments / ReplicaSets / StatefulSets whose ready < desired. |

## CNPG Postgres

| Script | Purpose |
|---|---|
| `check-postgres-dbs.sh` | Show Status / Instances / Ready instances for every CNPG cluster. |
| `check-cnpg-soak.sh` | Post-rollout soak check (restarts, OOMs, memory headroom). |
| `kill-postgres-pod.sh` | Wipe a CNPG replica's PV+PVC+pod so cnpg re-clones from primary. |
| `onetime-cnpg-backup.sh` | Apply the one-time backup manifest. |

## Rook / Ceph

| Script | Purpose |
|---|---|
| `destroy-rook-ceph-cluster.sh` | Catastrophic: tear down the entire Ceph cluster. |
| `get-ceph-password.sh` | Print the Ceph dashboard admin password. |

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
| `netshoot.sh` | Spawn a `netshoot` shell in the `downloads` namespace. |

## GPU / NVIDIA

| Script | Purpose |
|---|---|
| `nvidia-runtime-run-nvidia-smi.sh` | Spawn a CUDA pod on a GPU node and run `nvidia-smi`. |
| `list_pods_on_nvidia_runtimeclass.sh` | List pods using `runtimeClassName: nvidia`. |
| `nvtop.sh` | ssh to worker8 and run `nvtop`. |

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

## Miscellaneous

| Script | Purpose |
|---|---|
| `frigate_copy_speed.sh` | Tail Frigate logs and print recording copy throughput. |
| `ollama-pull-models.sh` | Pull a predefined set of models into the Ollama instance. |
| `start-fullykiosk.sh` | adb-trigger Fully Kiosk on the `frameo` display. |
