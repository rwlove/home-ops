# Cluster upgrade automation

Reusable helpers for an in-place, one-node-at-a-time kubeadm **minor** upgrade,
distilled from the 2026-08-22 `v1.35.4 → v1.36.4` run. The canonical runbook —
phases, preflight, failure modes, and the **Optimizations & lessons** writeup —
lives in [`docs/src/cluster_upgrade.md`](../../docs/src/cluster_upgrade.md). Read
it before running anything here.

| Script | What it does |
| ------ | ------------ |
| `predownload.sh` | Warms every node's package cache (`dnf/apt --downloadonly`) in parallel. Zero cluster impact. Run to completion **first**. |
| `rolling-upgrade.sh` | Drives the fleet node-by-node with hard safety gates. Idempotent (skips nodes already at target) → safe to stop/relaunch. |

## Usage (sketch — read the runbook + edit for your topology)

```sh
export SECRET_DOMAIN=your.cluster.domain
export KVER=1.36.4

# 0. Preflight (manual, see runbook): etcd snapshot, stage repos, suspend
#    descheduler, kubeadm upgrade plan.
# 1. Set Longhorn for fast/robust drains (restore in Phase 5):
kubectl -n longhorn-system patch settings.longhorn.io node-drain-policy \
  --type=merge -p '{"value":"allow-if-replica-is-stopped"}'
kubectl -n longhorn-system patch settings.longhorn.io replica-replenishment-wait-interval \
  --type=merge -p '{"value":"30"}'
# 2. First control plane only — the manifest/etcd bump:
ssh root@master1.$SECRET_DOMAIN "kubeadm upgrade apply v$KVER --yes \
  --ignore-preflight-errors=CoreDNSUnsupportedPlugins,CoreDNSMigration --skip-phases=addon"
# 3. Warm caches, then roll the rest:
bash predownload.sh
bash rolling-upgrade.sh          # runs in the foreground; long — background it if you like
```

## Safety model

- **Gates between every node:** etcd quorum 3/3, ceph OSDs all-up + no PG/OSD
  warns (pre-existing `AUTH_INSECURE_*` tolerated), Longhorn no-faulted-volume
  floor. Any anomaly `die()`s the whole run rather than cascading.
- **Never two control planes at once;** the heaviest CP goes last.
- **`--skip-phases=addon`** on `upgrade apply` so kubeadm leaves the
  Flux-managed CoreDNS / Cilium-replaced kube-proxy untouched.

## The speed optimizations (why this run got ~4× faster after tuning)

1. **Parallel pre-download** — removes ~10 min of package download per node.
2. **Fast Longhorn clear by DELETE, not evict** — `longhorn_clear_node` deletes
   a node's replicas that have a healthy copy elsewhere (instant) instead of
   `evictionRequested` rebuild-first (~12–25 min/node). Longhorn re-replenishes
   afterward. **Requires operator sign-off for temporary reduced redundancy.**
3. **Faulted-only health gate** — replica *count* never blocks the upgrade; only
   a genuinely unavailable (0-replica) volume does.

## Known gotchas encoded here

- **Longhorn dual instance-managers** (data plane still on the old engine — see
  the engine-image page: old engine may hold all the replica references) leave a
  `PodDisruptionBudget` at `disruptionsAllowed:0` that blocks `kubectl drain`.
  `drain_node` deletes the node's *empty* IM pods first (`kubectl delete`
  bypasses the PDB; safe once running-replicas are 0). The real fix is a separate
  Longhorn **engine live-upgrade** of all volumes — do it after the k8s upgrade.
- **Cordoned node + deleted OSD** (from a drain fallback in an aborted run) →
  OSD can't reschedule → ceph stuck N-1/N. **Uncordon** to release it.
- **Stalled replica rebuild** (progress frozen, 0 MB/s) when the rebuild source
  was on an evicted node → **delete the stuck replica** to re-rebuild from a
  healthy one.
- **GPU/containerd appliance (DGX):** driver is firmware-coupled — do **not**
  let a blanket `apt upgrade` move it. Hold the driver + kernel packages;
  upgrade k8s only; handle driver currency via the vendor tool separately.

## Phase 5 — restore after the upgrade

- `node-drain-policy` → back to `block-if-contains-last-replica`
- `replica-replenishment-wait-interval` → back to `600`
- Resume descheduler; `tools/etcd-defrag.sh`; verify `flux get all -A`.
- Longhorn **engine upgrade** (old→new) as its own maintenance; then GC the old
  instance-managers.
