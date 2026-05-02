# Cluster Upgrade Runbook

This runbook covers an in-place rolling upgrade of the cluster, planned
in May 2026 to bring all nodes to a single k8s 1.34 patch + cri-o 1.34,
then to k8s 1.35.

The cluster runs kubeadm with stacked etcd and kube-vip (DaemonSet) for
the control-plane VIP at `192.168.6.1`.

## State at the start of the upgrade

| Aspect | Reality |
|--------|---------|
| Control plane | 3 nodes (master1/2/3), HA via kube-vip DaemonSet |
| Workers | 7 nodes (worker2-8), each runs a Ceph OSD |
| Special hardware | worker8 = NVIDIA GPU; worker4 = Frigate Coral USB + Intel GPU + vlan-security |
| k8s | All nodes on 1.34.x, drift across `.2`/`.6`/`.7` |
| cri-o | master1 on 1.34.2 (modern); all others on 1.28.4 (legacy `el8` build, outside skew) |
| OS | master1 on CentOS Stream 10; all others on CentOS Stream 9 |
| etcd | 3.6.5 across all masters, healthy |
| Storage | Rook/Ceph (`ceph-block`), Longhorn (per-app named SCs), Garage (S3) |
| GitOps | Flux pulls from `home-ops-kubernetes` GitRepository |

## Phase 0 — Pre-flight (✅ done)

- ✅ Master1 stale kube-vip static pod removed
  (`/root/kube-vip.yaml.removed-20260502` is the rollback breadcrumb)
- ✅ etcd snapshot saved off-cluster
  (`~/cluster-backups/etcd-20260502/snapshot-prephase0-20260502.db`)
- ✅ `isv_cri-o_stable_v1.34.repo` pre-staged on all nodes via dnf —
  master1 already had it from its earlier rebuild
- ✅ `descheduler` HelmRelease suspended via the `disable-descheduler`
  commit pattern. Resume in Phase 5.
- ✅ Cilium 1.19.3 confirmed compatible with k8s 1.35 (Rook 1.19.5,
  CNPG 1.29.0, Istio 1.29.2 also confirmed)
- ✅ kube-vip DaemonSet already on v1.1.2; Renovate is tracking it
- ✅ API deprecation grep clean — no core k8s alpha/beta apiVersions
  used outside of vendor CRDs

## Per-node procedure (used in Phases 1–3)

The cri-o package swap, kubeadm upgrade, and kubelet bump all happen
inside the same drain window per node, so we drain once per node.

### Order

1. `worker7` (canary — plainest config, no special hardware)
2. `worker6`
3. `worker5`
4. `worker3`
5. `worker2`
6. `worker4` — **pre-suspend Frigate, zigbee2mqtt, zwave-js-ui** via
   the `disable-<app>` GitOps pattern; expect brief recording /
   automation gap
7. `worker8` — NVIDIA. **Pre-suspend ollama, comfyui.** Carefully port
   the NVIDIA runtime stanza to a drop-in. Smoke-test with a
   `runtimeClassName: nvidia` pod before un-suspending.
8. `master3`
9. `master2`
10. `master1` (last — the VIP risk is bounded by master2/master3 kube-vip
    DaemonSet pods that we verified are healthy)

Never drain two masters concurrently. After each master, verify etcd
quorum:

```sh
kubectl exec -n kube-system etcd-master1.thesteamedcrab.com -- etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  endpoint status --cluster -w table
```

### Per-worker procedure

For each worker, in order:

1. **Pre-flight per-node**:
   ```sh
   # If the worker hosts a CNPG primary, fail it over first
   for primary in $(kubectl get pod -n databases -l 'cnpg.io/instanceRole=primary' \
       --field-selector spec.nodeName=<worker>.thesteamedcrab.com -o name); do
     cluster=$(kubectl get $primary -n databases -o jsonpath='{.metadata.labels.cnpg\.io/cluster}')
     kubectl cnpg promote -n databases "$cluster" <some-replica>
   done

   # Set ceph noout (skips OSD rebalance during the planned outage)
   kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph osd set noout

   # If the worker hosts hardware-pinned single-replica pods,
   # commit a `disable-<app>` PR first (suspend their HelmRelease).
   ```

2. **Drain** (lets PDBs do their job):
   ```sh
   kubectl drain <worker>.thesteamedcrab.com \
     --ignore-daemonsets --delete-emptydir-data
   ```

3. **On the node — package swap + kubeadm upgrade**:
   ```sh
   ssh root@<worker>.thesteamedcrab.com '
     # Get rid of the legacy crio.conf / .rpmnew so the new package can drop a clean drop-in
     rm -f /etc/crio/crio.conf /etc/crio/crio.conf.rpmnew /etc/crio/crio.conf.working

     # Swap the cri-o package: removes legacy 1.28.4 (el8 / kubic repo) and installs new 1.34.x
     dnf -y swap cri-o-1.28.4 cri-o
     dnf clean all && dnf makecache

     # Upgrade kubeadm + kubelet + kubectl to the latest 1.34 (or 1.35 in Phase 3)
     dnf -y install kubeadm-1.34.7 kubelet-1.34.7 kubectl-1.34.7

     # Run kubeadm upgrade for this worker (no-op for cri-o swap; needed for kubelet config in Phase 3)
     kubeadm upgrade node

     systemctl daemon-reload
     systemctl restart crio
     systemctl restart kubelet
   '
   ```

4. **Smoke-test the node before uncordon**:
   ```sh
   ssh root@<worker>.thesteamedcrab.com '
     crictl info | grep -E "runtimeName|systemd|cgroupDriver"
     crictl images | head
   '
   kubectl get node <worker>.thesteamedcrab.com -o wide
   ```

5. **Uncordon**:
   ```sh
   kubectl uncordon <worker>.thesteamedcrab.com
   kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph osd unset noout
   ```

6. **Wait** for `ceph -s` to return `HEALTH_OK` before proceeding to
   the next node.

### Worker8 (NVIDIA) extra steps

After step 3 above, drop in the NVIDIA runtime config:

```sh
ssh root@worker8.thesteamedcrab.com 'cat > /etc/crio/crio.conf.d/20-nvidia.conf <<EOF
[crio.runtime.runtimes.nvidia]
runtime_path = "/usr/bin/nvidia-container-runtime"
runtime_root = "/run/nvidia"
runtime_type = "oci"
EOF
systemctl restart crio'
```

Verify with a smoke-test pod before resuming ollama / comfyui:

```sh
kubectl run nvidia-smoke --rm -i --restart=Never \
  --overrides='{"spec":{"runtimeClassName":"nvidia","nodeName":"worker8.thesteamedcrab.com"}}' \
  --image=nvidia/cuda:12.0-base-ubuntu22.04 -- nvidia-smi
```

### Master procedure

Same as worker, with two differences:

- **Master1 is special** (already on cri-o 1.34.x). Skip the cri-o swap;
  just do the k8s patch / minor bump. Master1 is also the last in the
  order so the kube-vip VIP can fail over to master2/master3 during
  master1's drain.
- **First control plane in Phase 3 minor bump** uses
  `kubeadm upgrade apply v1.35.x`; the others use
  `kubeadm upgrade node`. The first one *must* be done before any
  others.

## Phase 5 — verify and clean up

- All nodes show same kubelet + cri-o version: `kubectl get nodes -o wide`
- Flux reconciled: `flux get all -A | grep -v True`
- `ceph -s` is `HEALTH_OK`
- Run `tools/etcd-defrag.sh` (etcd grew during the upgrade)
- **Resume descheduler**: `git revert <disable-descheduler sha>` and
  push. Same for any hardware-pinned `disable-<app>` commits made
  along the way.
- Update this runbook with anything new that bit you, before memory
  fades.

## Rollback

In-place RPM downgrade is messy, especially for kubelet across a minor
boundary. Realistic rollback paths:

- Per-node, *before* `kubeadm upgrade apply` on the first master: roll
  back is just `dnf downgrade kubeadm kubelet kubectl` to 1.34 + put
  the old `crio.conf` back (it's saved as `crio.conf.rpmsave` after the
  package swap).
- Per-node, *after* `kubeadm upgrade apply`: bring forward the rest of
  the cluster. Don't try to roll back the apiserver minor.
- Catastrophic (etcd corruption, control plane unrecoverable): restore
  from `~/cluster-backups/etcd-20260502/snapshot-prephase0-20260502.db`
  via [the kubeadm etcd recovery procedure][1]. **This is a last
  resort and has not been tested in this homelab.** It assumes you
  have at least one master with the original PKI intact and can
  `etcdctl snapshot restore` to a fresh data dir, then restart etcd
  static pods.

[1]: https://kubernetes.io/docs/tasks/administer-cluster/configure-upgrade-etcd/#restoring-an-etcd-cluster
