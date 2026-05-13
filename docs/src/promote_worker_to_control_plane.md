# Promote a Worker to a Schedulable Control Plane

End-to-end procedure for converting a bare-metal worker into a third
(or replacement) control-plane node, while removing an existing
control plane (typically a VM master that needs to retire).

The worked example below promotes `worker2.${SECRET_DOMAIN}` and
retires the VM `master2.${SECRET_DOMAIN}`. Substitute names freely —
nothing in the procedure is specific to those hosts.

## Why this exists

The control plane in this cluster runs three etcd voters. When two of
those live on the same physical host (e.g. master2 + master3 as VMs on
one box), one hardware failure costs you etcd quorum. Promoting a
bare-metal worker into the control-plane pool fixes that without
changing the etcd voter count.

The promoted node stays **schedulable** — control-plane and worker
roles are stacked on the same host, the same way master1 already runs
in this cluster.

## What you need to know first

This cluster is **kubeadm + kube-vip + cri-o + Cilium** on CentOS
Stream. Relevant facts:

- Control-plane VIP is `192.168.6.1:6443` (kube-vip). Clients keep
  their kubeconfigs unchanged through this whole procedure.
- kube-vip is a static pod under `/etc/kubernetes/manifests/kube-vip.yaml`
  on every control-plane node. New control planes need this file
  themselves; see `init/kube-vip.sh` for the canonical generator.
- kubelet-csr-approver is running, so node CSRs auto-approve. Manual
  approval (`init/approve-csrs.sh`) is the fallback.
- `kubeadm-config` ConfigMap in `kube-system` carries the list of
  control-plane endpoints. Old members must be removed from it.

## Hostname strategy

"Convert worker2 to master2" can mean two things. **Default to (a)**
unless you have a concrete reason to rename:

- **(a) Promote in place.** Keep the hostname (`worker2.${SECRET_DOMAIN}`).
  The `master`/`worker` prefix is purely cosmetic; the
  `node-role.kubernetes.io/control-plane` label is what matters.
- **(b) Rename to `masterN.${SECRET_DOMAIN}`.** Requires
  regenerating client certs, a new etcd member name, a new kube-vip
  pod identity, and DNS coordination. High risk, no functional gain.

The procedure below assumes **(a)**.

## Preconditions

Do not start until all of these are true:

1. **Surviving etcd voters are healthy as voters.** This is the load-bearing
   check, because Phase 1 takes you to a 2/2 etcd quorum. What matters is
   *write performance*, not just "is the node Ready":
   - On each surviving control-plane node, the etcd container should
     show no recent `apply request took too long` or `waiting for
     ReadIndex response took too long` warnings:
     ```sh
     kubectl -n kube-system logs etcd-<node> --since=1h \
       | grep -E 'took too long|ReadIndex'
     ```
     A handful per hour is normal; sustained streams mean the voter is
     limping and you should not start the procedure.
   - On each surviving control-plane node, check disk latency on the
     device hosting `/var/lib/etcd` (often the OS LVM root, *not*
     necessarily the same disk as Longhorn or Ceph): `iostat -xz 5` and
     watch `await`/`%util`. Sustained `await` > a few ms on the etcd
     device is a problem; etcd wants single-digit-ms fsync.
   - A failed *secondary* disk on a control-plane node (e.g. a SATA
     drive that dropped off the bus while etcd lives on NVMe) is not
     an automatic blocker — but check whether the failure is causing
     bus resets or kernel I/O retries that bleed into the etcd device.
2. **Ceph is HEALTH_OK.** Slow-OSD alerts on a control-plane node may
   *or may not* indicate disk failure (Ceph and Longhorn often share an
   NVMe — contention can produce slow ops without a dying disk). Either
   way, do not stack: an unhealthy Ceph plus a transient 2-voter window
   plus an OSD coming out for the worker drain is too many concurrent
   degradations.
3. **No long-running storage operations are in flight** that touch the
   worker being drained — e.g. an Immich offsite seed, a CNPG full
   backup, a Longhorn rebuild.
4. **Etcd snapshot saved off-host.** See [Etcd snapshot](#etcd-snapshot)
   below.
5. **Decision made on the retiring control plane.** Either remove it
   first (briefly run on 2 voters), or defer it until after the new
   control plane is healthy (briefly run on 4 voters). Removing first
   is preferred — a 4-member etcd adds a write-quorum cost without
   improving fault tolerance.
6. **Kubelet/kubeadm version match.** Confirm the worker's kubelet
   and kubeadm packages match what's currently on the control plane.
   Mismatched skew causes join to fail mid-handshake.
7. **Firewalld is disabled** on the worker (per cluster policy —
   firewalld silently drops kubelet traffic post-reboot).

## Etcd snapshot

Take from a healthy member (typically the leader; identify with
`etcdctl endpoint status --cluster`):

```sh
kubectl -n kube-system exec etcd-master1.${SECRET_DOMAIN} -- \
  etcdctl --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/peer.crt \
  --key=/etc/kubernetes/pki/etcd/peer.key \
  snapshot save /var/lib/etcd-backup/pre-cp-swap-$(date +%F).db
```

Copy the snapshot off the host before continuing.

## Audit what the worker is carrying

Before you drain, inventory anything hardware-pinned or
single-replica:

- **USB devices** (Z-Wave dongles, etc.) attached to the worker
  pin pods to it via NFD `usb-*` feature labels. The hardware stays
  with the box; if you reuse the box for control plane, those pods
  return after rejoin. If you physically move the box, plan to move
  the dongles too.
- **GPU resources** (`gpu.intel.com/i915`, NVIDIA, Coral) — same
  story: stay with the box, re-published after rejoin.
- **Custom node labels** (`node.network/vlan-*`, app-specific
  pinning labels) — capture them now, re-apply in
  [Phase 4](#phase-4--make-it-schedulable--restore-roles).
- **CNPG primaries** — failover-friendly but you may want to manually
  promote a replica off the worker first to control timing.
- **Single-replica controllers** — istiod, MCP gateways, etc. These
  will reschedule but cause brief disruption to mesh-injection or
  tool calls.
- **Ceph OSD on the worker** — needs `ceph osd out N` and rebalance
  before drain.
- **Ceph mon on the worker** — Rook will recreate it on a different
  node automatically, but cluster runs at 2/3 mons in the gap.

Capture the worker's labels:

```sh
kubectl get node worker2.${SECRET_DOMAIN} -o yaml > /tmp/worker2-pre-drain.yaml
```

## Phase 1 — Retire the outgoing control plane

Skip this phase if you're adding a 4th control plane and removing the
old one later.

1. Drain the outgoing master:
   ```sh
   kubectl drain master2.${SECRET_DOMAIN} --ignore-daemonsets --delete-emptydir-data
   ```
2. Reset kubeadm state on the host:
   ```sh
   ssh master2 'kubeadm reset --force && systemctl stop kubelet'
   ```
   Do **not** wipe `/var/lib/etcd` yet — it's your in-place rollback
   if the new control plane fails to join.
3. Remove the etcd member (run from a remaining control plane):
   ```sh
   etcdctl ... member list                    # find master2's member ID
   etcdctl ... member remove <member-id>
   ```
4. Update kubeadm bookkeeping:
   ```sh
   kubectl -n kube-system edit cm kubeadm-config   # drop master2 endpoint
   kubectl delete node master2.${SECRET_DOMAIN}
   ```
5. Power down the VM. Keep the VM image around for 24h as rollback.

You're now on 2 control planes. Etcd quorum is 2/2 — any further loss
fails writes. Don't dawdle.

## Phase 2 — Drain the worker cleanly

1. Optionally suspend Flux for noisy releases that you don't want
   re-reconciling mid-drain:
   ```sh
   flux suspend kustomization <name>
   ```
2. Cordon: `kubectl cordon worker2.${SECRET_DOMAIN}`
3. Out the OSD and wait for Ceph to rebalance:
   ```sh
   kubectl -n rook-ceph patch deploy rook-ceph-osd-N --replicas=0 \
     --type=merge -p '{"spec":{"replicas":0}}'
   # from a working ceph CLI (e.g. rook-ceph-tools pod):
   ceph osd out N
   ceph -w   # wait for HEALTH_OK and PGs active+clean
   ```
4. Move CNPG primaries off the worker:
   ```sh
   kubectl cnpg promote <cluster> <healthy-replica-instance>
   ```
   Or accept the brief failover window during drain.
5. Drain:
   ```sh
   kubectl drain worker2.${SECRET_DOMAIN} \
     --ignore-daemonsets --delete-emptydir-data --force
   ```
6. Remove from the cluster:
   ```sh
   kubectl delete node worker2.${SECRET_DOMAIN}
   ```
7. On the host, reset kubeadm state but **preserve Longhorn data**:
   ```sh
   kubeadm reset --force
   rm -rf /etc/kubernetes /var/lib/etcd /var/lib/kubelet
   # leave /var/lib/longhorn alone — replicas re-attach after rejoin
   ```

## Phase 3 — Rejoin as a control plane

1. From an existing control plane, generate fresh join material:
   ```sh
   CERT_KEY=$(kubeadm init phase upload-certs --upload-certs \
     --config /path/to/init/clusterconfiguration.yaml | tail -1)
   JOIN=$(kubeadm token create --print-join-command)
   echo "$JOIN --control-plane --certificate-key $CERT_KEY"
   ```
2. On the worker (still its original hostname), preflight the kernel:
   ```sh
   modprobe br_netfilter
   echo 1 > /proc/sys/net/ipv4/ip_forward
   ```
3. Run the printed join command. After it completes:
   ```sh
   mkdir -p /etc/kubernetes/manifests
   ```
4. **Drop a kube-vip static pod manifest** into
   `/etc/kubernetes/manifests/kube-vip.yaml`. Mirror what
   `init/kube-vip.sh` produces — but verify the `INTERFACE` env var
   matches *this* host's NIC (master1 uses `enp0s31f6`; the new
   control plane may differ). Without kube-vip on the new master, the
   VIP just stays put on the survivors — non-fatal, but you lose the
   new node from leader election.
5. Approve any pending CSRs (kubelet-csr-approver should auto-approve;
   check anyway):
   ```sh
   kubectl get csr | grep Pending
   ./init/approve-csrs.sh   # if needed
   ```

## Phase 4 — Make it schedulable + restore roles

1. Remove the control-plane NoSchedule taint:
   ```sh
   kubectl taint nodes worker2.${SECRET_DOMAIN} \
     node-role.kubernetes.io/control-plane:NoSchedule-
   ```
2. Re-apply node labels you captured in
   [Audit](#audit-what-the-worker-is-carrying). NFD will re-publish
   hardware feature labels on its own; you only need to re-apply the
   manual ones:
   ```sh
   kubectl label nodes worker2.${SECRET_DOMAIN} \
     node.longhorn.io/create-default-disk=true \
     node.network/vlan-iot=true \
     node.network/vlan-security=true
   ```
3. Recreate Ceph OSD. With `useAllNodes`/`useAllDevices` enabled, Rook
   picks the disk back up automatically. Otherwise add the host to the
   `CephCluster` `nodes` list.
4. Resume any Flux kustomizations you suspended in Phase 2.

## Phase 5 — Verify

- `kubectl get nodes -o wide` — promoted node has `control-plane`
  role and is `Ready`.
- `flux get all -A | grep -v True` is empty (or only known-suspended
  entries).
- Etcd shows three members:
  ```sh
  etcdctl ... member list
  etcdctl ... endpoint status --cluster -w table
  ```
- Ceph: `ceph -s` HEALTH_OK, all OSDs in/up, all PGs `active+clean`.
- VIP serving from the new node too:
  ```sh
  curl -k https://192.168.6.1:6443/healthz
  ```
- Hardware-pinned pods returned (e.g. `zwave-js-ui-0`, GPU
  transcoders).

## Rollback

The cheapest rollback is **before Phase 3 step 3** (the new
`kubeadm join`). Up to that point:

- The old VM master is powered off but its `/var/lib/etcd` is intact.
  Power it back on, restart kubelet, and it will re-join the etcd
  cluster on its old member ID — *if* you haven't yet removed it via
  `etcdctl member remove`. After member-remove, you have to
  re-bootstrap that member.
- The worker has been drained but not yet re-joined. `kubectl
  uncordon` is enough to put it back in service as a worker — but
  you'll need to re-add the labels you removed.

After the new control plane has been healthy for ~24h, the old VM's
etcd data dir can be wiped and the VM image deleted.

## What this does NOT solve

This procedure swaps one control-plane host. If two of your three
control planes still share a single failure domain (e.g. master3
remains a VM on the same host as the retired master2 *was*), repeat
the procedure for master3 against another bare-metal worker. Only then
does the control plane have three independent failure domains.
