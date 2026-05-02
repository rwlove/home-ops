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

Standard "clean RPM" workers first, then the special cases, then masters.

1. `worker7` ✅ migrated 2026-05-02 — k8s 1.34.7 + cri-o 1.34.7
2. `worker3` — Intel GPU label but no pinned pods; **mon-f is pinned
   here, drain only when worker3 is the active mon target** (see
   "mon nodeSelector trap" below)
3. `worker2`
4. `worker4` — Frigate node. **Pre-suspend frigate, zigbee2mqtt,
   zwave-js-ui** via the `disable-<app>` GitOps pattern; expect brief
   recording / automation gap.
5. `worker8` — NVIDIA. **Pre-suspend ollama, comfyui.** Carefully
   port the NVIDIA runtime stanza to a drop-in. Smoke-test with a
   `runtimeClassName: nvidia` pod before un-suspending.
6. `worker6` — **manual-install kubelet/cri-o**, requires the
   alternate procedure below (no `rm crio.conf`, use `dnf install`).
7. `worker5` — same alternate procedure as worker6.
8. `master3`
9. `master2` (kubelet already 1.34.7; cri-o still 1.28.4)
10. `master1` (last — already on 1.34.2 + cri-o 1.34.2; just kubelet
    patch bump). The VIP risk is bounded by master2/master3 kube-vip
    DaemonSet pods.

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

### Pre-flight before every node (lessons from 2026-05-02)

These checks **must** happen before draining any node. Skipping any of
them caused real damage on the first attempt at this phase.

1. **Drain Longhorn replicas off the target node FIRST, before
   `kubectl drain`.** Otherwise: when the node's longhorn-manager
   blips during the cri-o restart, every replica still on that node
   becomes inaccessible, and pods on OTHER nodes whose replicas are
   here go into CrashLoopBackOff. That cascades into CNPG PDBs
   blocking subsequent drains. Two ways to do this:

   **Via Longhorn UI** (preferred — visual confirmation):
   - Open the Longhorn UI (port-forward `longhorn-frontend` in
     `longhorn-system` if you don't have ingress wired up).
   - Node tab → click target node → "Edit Node" → set "Node
     Scheduling" to **Disable** AND "Eviction Requested" to **True**.
   - Watch the Volume tab — every volume with a replica on this node
     should show its replica count restoring on other nodes.
   - When the target node's "Replicas" count reaches 0, proceed.
   - **Re-enable scheduling and clear eviction after the node is back
     and you've uncordoned it**, otherwise replicas won't return.

   **Via kubectl** (equivalent):
   ```sh
   kubectl patch -n longhorn-system nodes.longhorn.io <node> \
     --type=merge -p '{"spec":{"allowScheduling":false,"evictionRequested":true}}'

   # Wait until no replicas remain on the node
   until [ "$(kubectl get replicas.longhorn.io -n longhorn-system -o json |
     jq -r --arg n "<node>" '.items[] | select(.spec.nodeID==$n) | .metadata.name' |
     wc -l)" = "0" ]; do sleep 10; done

   # … do the drain + upgrade + uncordon …

   # After uncordon and node Ready, restore Longhorn scheduling:
   kubectl patch -n longhorn-system nodes.longhorn.io <node> \
     --type=merge -p '{"spec":{"allowScheduling":true,"evictionRequested":false}}'
   ```

2. **Wait for `ceph -s` HEALTH_OK** before draining the next node.
   Not just "the previous node's OSD pod is Ready" — Rook creates
   dynamic per-host OSD PDBs (`rook-ceph-osd-host-<host>`) when an
   OSD is unavailable, with `MAX UNAVAILABLE: 1, ALLOWED DISRUPTIONS:
   0`. While ANY OSD-host PDB exists for ANY host, the next drain
   will hang indefinitely on its own host's PDB. This cost ~20
   minutes of stuck drain on worker3 because worker6 was still
   degraded in the background.

3. **Check mon nodeSelectors and don't drain a mon's pinned host
   while another mon is also down.** Rook pins each mon to a
   specific node and recreates them under new letters as nodes drop
   in/out:
   ```sh
   kubectl get pod -n rook-ceph -l app=rook-ceph-mon -o jsonpath='{range .items[*]}{.metadata.labels.mon}{"\t"}{.spec.nodeSelector.kubernetes\.io/hostname}{"\n"}{end}'
   ```
   Re-check before every node — the mon names shift (we saw c,e,f →
   c,e,g → c,e,h over a single afternoon as nodes drained). Draining
   a node hosting a pinned mon strands that mon — it cannot
   reschedule until the pin is satisfied again. **If two mons are
   pinned to drained nodes, ceph quorum is lost.** Drain pinned-mon
   nodes one at a time and let the mon come back before touching the
   next.

4. **Check the package install style on the target node.** Some
   nodes have `kubelet/kubeadm/kubectl/cri-o` installed via dnf
   (RPM-tracked). Others have manually-installed binaries (no RPM
   entries):
   ```sh
   ssh root@<node> 'rpm -qa | grep -cE "^(kubeadm|kubelet|kubectl|cri-o)-"'
   ```
   Returns 4 → standard procedure. Returns 0 → **alternate
   procedure** (don't `rm crio.conf`; use `dnf install` not `dnf
   upgrade`). Nodes known to be manual-install: worker5, worker6.

5. **CNPG primary failover**:
   ```sh
   for c in $(kubectl get pod -n databases -l 'cnpg.io/instanceRole=primary' \
       --field-selector spec.nodeName=<node>.thesteamedcrab.com \
       -o jsonpath='{range .items[*]}{.metadata.labels.cnpg\.io/cluster}{"\n"}{end}'); do
     replica=$(kubectl get pod -n databases -l "cnpg.io/cluster=$c,cnpg.io/instanceRole=replica" \
       -o jsonpath='{range .items[?(@.spec.nodeName!="<node>.thesteamedcrab.com")]}{.metadata.name}{"\n"}{end}' | head -1)
     kubectl cnpg promote -n databases "$c" "$replica"
   done
   ```
   Then poll `kubectl get pod -n databases -l 'cnpg.io/instanceRole=primary' --field-selector spec.nodeName=<node>...`
   until empty.

6. **Hardware-pinned pod suspension** (worker4: frigate +
   zigbee2mqtt + zwave-js-ui; worker8: ollama + comfyui). Use the
   `disable-<app>` GitOps commit pattern, not `kubectl scale` —
   Flux will revert imperative scales. Wait for Flux reconciliation
   to actually take the pods down before draining.

### Standard per-worker procedure (RPM-tracked nodes)

For workers with RPM entries (rpm-qa returns 4 packages):

1. Pre-flight checks above.
2. **Drain**:
   ```sh
   kubectl drain <worker>.thesteamedcrab.com \
     --ignore-daemonsets --delete-emptydir-data
   ```
3. **Package upgrade and kubelet config refresh**:
   ```sh
   ssh root@<worker>.thesteamedcrab.com '
     # Drop the legacy crio.conf / .rpmnew. Order matters: only do
     # this RIGHT BEFORE the upgrade succeeds — leaving cri-o
     # without a config will trigger the "unsafe procfs detected"
     # runc error and break ALL pods on the node.
     rm -f /etc/crio/crio.conf /etc/crio/crio.conf.rpmnew /etc/crio/crio.conf.working

     # Single transaction: cri-o upgrade picks up the new isv repo
     # automatically since 1.34.7 > 1.28.4.
     dnf upgrade -y cri-o kubelet-1.34.7 kubeadm-1.34.7 kubectl-1.34.7

     kubeadm upgrade node
     systemctl daemon-reload
     systemctl restart crio
     systemctl restart kubelet
   '
   ```
4. **Smoke-test before uncordon**:
   ```sh
   ssh root@<worker>.thesteamedcrab.com '
     systemctl is-active crio kubelet
     crictl info | grep -E "CgroupManagerName|DefaultRuntime"
     ls /etc/crio/crio.conf.d/   # should exist; legacy crio.conf should be gone
   '
   kubectl get node <worker>.thesteamedcrab.com -o wide   # version + cri-o version match expected
   ```
5. **Uncordon**:
   ```sh
   kubectl uncordon <worker>.thesteamedcrab.com
   ```
   Rook auto-clears any host noout flag on its own a few seconds
   after uncordon — don't manually unset it.
6. **Wait for `ceph -s` HEALTH_OK** (no OSDs down, no degraded PGs)
   before the next node. Do not skip this. Typically ~1–3 minutes.

### Alternate procedure for manual-install nodes (worker5, worker6)

These nodes have kubelet/cri-o binaries in `/usr/bin/` not tracked by
RPM. `dnf upgrade` cannot upgrade what it cannot see, and `rm
crio.conf` will break the node since the dnf step provides no
replacement.

1. Pre-flight checks (same as above).
2. **Drain** (same as above).
3. **Fresh-install via dnf** (overwrites the un-tracked binaries):
   ```sh
   ssh root@<worker>.thesteamedcrab.com '
     # Stop services so we can replace running binaries cleanly
     systemctl stop kubelet
     systemctl stop crio

     # Move the manual binaries aside (rollback if dnf install fails)
     mv /usr/bin/kubelet  /usr/bin/kubelet.manual
     mv /usr/bin/kubeadm  /usr/bin/kubeadm.manual
     mv /usr/bin/kubectl  /usr/bin/kubectl.manual
     mv /usr/bin/crio     /usr/bin/crio.manual

     # Install the RPMs fresh (now they will be tracked)
     dnf install -y cri-o kubelet-1.34.7 kubeadm-1.34.7 kubectl-1.34.7

     # Now we can safely remove crio.conf — package provides drop-in
     rm -f /etc/crio/crio.conf /etc/crio/crio.conf.rpmnew /etc/crio/crio.conf.working

     kubeadm upgrade node
     systemctl daemon-reload
     systemctl restart crio
     systemctl restart kubelet

     # Verify and clean up rollback files only after success
     rpm -q cri-o kubelet kubeadm kubectl
     # rm /usr/bin/*.manual    # only after smoke-test confirms success
   '
   ```
4. Smoke-test, uncordon, wait for `HEALTH_OK` — same as above.

### Worker8 (NVIDIA) extra steps

After the standard procedure:

```sh
ssh root@worker8.thesteamedcrab.com 'cat > /etc/crio/crio.conf.d/20-nvidia.conf <<EOF
[crio.runtime.runtimes.nvidia]
runtime_path = "/usr/bin/nvidia-container-runtime"
runtime_root = "/run/nvidia"
runtime_type = "oci"
EOF
systemctl restart crio'
```

Smoke-test before resuming ollama / comfyui:

```sh
kubectl run nvidia-smoke --rm -i --restart=Never \
  --overrides='{"spec":{"runtimeClassName":"nvidia","nodeName":"worker8.thesteamedcrab.com"}}' \
  --image=nvidia/cuda:12.0-base-ubuntu22.04 -- nvidia-smi
```

### Failure modes seen on 2026-05-02

| Symptom | Root cause | Fix |
|---------|-----------|-----|
| Drain hangs ~indefinitely on `rook-ceph-osd-host-*` PDB | A previous node's OSD is still degraded; Rook's per-host PDB blocks all OSD evictions cluster-wide | Wait for `ceph -s` HEALTH_OK, then retry drain |
| New pods on a node fail with `runc create failed: unsafe procfs detected` | `/etc/crio/crio.conf` was removed but the new cri-o package didn't install | Restore `crio.conf` from a peer node's identical version (`scp root@<peer>:/etc/crio/crio.conf root@<broken>:/etc/crio/`), `systemctl restart crio` |
| `mon-X` stays Pending after drain | Mon is pinned via `nodeSelector` to the cordoned node | Uncordon the pinned node, mon comes back. Don't drain another mon's host until quorum is restored |
| `dnf upgrade` reports `kubelet-1.34.7: No match for argument` | Node has manually-installed kubelet (no RPM entry) | Use the alternate procedure (`dnf install` after moving binaries aside) |
| `longhorn-manager-X` stuck CrashLoopBackOff with `bind: address already in use` on port 9502 | Old longhorn-manager process orphaned by a previous container; cri-o lost track of it but the binary is still bound | `ssh root@<node> 'pgrep -af "longhorn-manager -d daemon"'` → `kill -9 <pid>`; then `kubectl delete pod -n longhorn-system longhorn-manager-X` |
| Multiple CNPG replica pods stuck Init:CrashLoopBackOff on a recently-broken node | Their Longhorn volumes failed to attach during the node's outage; pods are now in 5-minute kubelet backoff | Once Longhorn recovers, `kubectl delete pod` each one to force immediate retry. Volume attaches succeed. |
| Replicas-cascading-CNPG-PDB-blocks-drain | One replica unhealthy in cluster X means PDB has 0 disruptions; subsequent drain anywhere blocks on cluster X's PDB | Heal the unhealthy replica before draining its peer's host. The Longhorn pre-drain step (above) prevents this. |
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
