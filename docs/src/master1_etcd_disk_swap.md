# master1 etcd-Disk Swap Plan

> **Status: PLAN — still not executed as of 2026-11-12** (captured
> 2026-05-05). master1 has not been reinstalled and remains the slow
> etcd voter; the 2026-08-09 raft-timer tuning is the applied *stopgap*,
> not the fix (see [etcd raft timers](etcd_raft_timers.md)). Re-verify
> the drive model + pricing below before ordering.

Active plan to fix master1's degraded etcd performance by replacing
the NVMe under `/`. Captured 2026-05-05 from a diagnostic session.

## Why

master1's etcd is the slow voter and the cause of intermittent
apiserver timeouts on this cluster. Confirmed via Prometheus:

| 10m p99 | master1 (192.168.1.9) | master2 (192.168.4.9) | master3 (192.168.4.10) |
|---|---|---|---|
| `etcd_disk_wal_fsync` | **31 ms** | 17 ms | 16 ms |
| `etcd_disk_backend_commit` | **103 ms** | 29 ms | 27 ms |
| `etcd_server_slow_apply_total` (cumulative) | **67,523** | 21,459 | 5,738 |

Read latency on the same NVMe is sub-millisecond. The slowness is on
the **sync-write path** (page cache doesn't help `fdatasync`). The
suspected drive is the budget **HS1TBNVME M2 1TB** (41% wear at the
time), hosting the LVM root that contains `/var/lib/etcd`.

> **DANGER — identify drives by model/serial, never by `nvmeXn1`.**
> master1's NVMe kernel names **swap between boots**. When this page was
> written the HS1TBNVME was `nvme1n1` and the WD Blue SN570 was
> `nvme0n1`; on 2026-11-21 it was the reverse. The WD Blue carries the
> Ceph OSD (partition 1) and `/var/lib/longhorn` (partition 2) — pointing
> a `dd`/`pvmove`/wipe at the wrong name destroys production data. Always
> resolve by stable path first — `ls -l /dev/disk/by-id/ | grep -E 'HS1TB|SN570'`
> and `pvs -o pv_name,vg_name` (`cs_master1` must sit on the HS1TBNVME).
>
> - **Replace:** HS1TBNVME 1TB, serial `110101000500` (root LVM `cs_master1`, etcd)
> - **Keep:** WD Blue SN570 2TB, serial `23172Q802011` (Ceph OSD by-id
>   `nvme-WD_Blue_SN570_2TB_23172Q802011_1-part1`, Longhorn on part2)
>
> 2026-11-21: this drive is also the root cause of the API-VIP flaps —
> master1's etcd member had 11k slow read-index/14 d vs ~200 on peers
> (see the kube-vip lease-timing change, #16215).

Cluster has a leader, zero leader changes/hr, no failed proposals —
etcd is stable but degraded. Apiserver-master1 takes the brunt
because it always reads from local etcd.

Unrelated note: `/dev/sdb` on master1 is in `XfsShutdown` state — a
secondary SATA disk that fell off the bus. **Does not affect etcd**
(verified: nothing on sdb is mounted; etcd lives on `/`, OSD-1 lives
on the WD Blue's partition 1, Longhorn on its partition 2 — kernel name
was `nvme0n1` at the time, see the warning above). Investigate on its
own schedule.

## Drive choice (decided 2026-11-21)

**Samsung 990 PRO 1TB (MZ-V9P1T0)**, M.2 2280, PCIe 4.0, DRAM-cached TLC.

- **Why not the Micron 7450 PRO (PLP):** the 2026 NAND crunch priced
  enterprise PLP M.2 out ($645–913 for 480 GB on 2026-11-21, vs ~$200 in
  May). Power-loss durability comes from UPS + clean shutdown instead (see
  [Follow-ups](#follow-ups)); the etcd *latency* problem is fixed by a
  DRAM-cached drive, which is what the current budget drive lacks.
- **Slot:** both M.2 slots on master1 (ThinkCentre M910x, board 310C) are
  **PCIe 3.0 x4** (Intel 200-series root ports, 8.0 GT/s x4, measured
  2026-11-21). A PCIe 4.0/5.0 drive runs at 3.0 — irrelevant for etcd, which
  needs low-latency `fdatasync`, not bandwidth. No heatsink variant (does not
  fit the Tiny chassis).
- **Capacity:** `/` uses ~122 GB; 1 TB is ample.

## Why Option B (clone) is out

The new drive is **smaller** than the old one: the HS1TBNVME is
2,000,409,264 sectors (~1.024 TB); a 990 PRO 1TB is 1,953,525,168 sectors
(1.000 TB). The root LV is XFS and fills the disk, and **XFS cannot shrink**,
so a block-level `dd`/`pvmove` clone cannot fit. Both M.2 slots are also
occupied, so the drives can't be attached side by side without pulling the
WD Blue. **Reinstall + rejoin (below) is the procedure.**

## Swap-day procedure

Budget **~3 hours** hands-on. Pick a quiet window. master1 is a
schedulable control plane, so its workloads move elsewhere during the swap;
the RTL-SDR receiver (USB dongle on master1) is down until master1 rejoins.

!!! warning "Single point of failure during the window"
    From Phase 3 until master1 rejoins, etcd runs on **2 of 2** voters —
    master2 and master3, **both VMs on beast**. Any beast hiccup in that
    window stops the API (running workloads keep running; nothing can be
    scheduled or changed). Don't do anything to beast during the swap, and
    don't stretch the window.

### Phase 0 — Before swap day (no impact)

1. Confirm the drive is the **990 PRO** (MZ-V9P1T0), not a 990 EVO / EVO
   Plus (DRAM-less).
2. Write a **CentOS Stream 10** x86_64 installer to USB (master1 runs CS10
   today).
3. **Host config capture.** A read-only capture of master1's host config
   (no PKI, no SSH host keys) was taken 2026-11-21 to the operator laptop
   (`~/master1-swap-2026-11/`, mode 600 — never commit it): NetworkManager
   connections (including the VLAN 20/40 sub-interfaces), `/etc/crio`,
   `/etc/containers` (registry mirror config), `/etc/sysctl.d`,
   `/etc/udev/rules.d` (RTL-SDR rules), `/etc/yum.repos.d`, `chrony.conf`,
   `fstab`, `/etc/default/grub`, root `authorized_keys`, the kubelet config,
   the package list (`rpm-names.txt`) and enabled units. **Re-run it on swap
   day** to catch drift:

   ```sh
   ssh root@master1 'cd / && tar czf - --ignore-failed-read \
     --exclude=etc/kubernetes/pki --exclude="etc/ssh/ssh_host_*" \
     etc/NetworkManager/system-connections etc/crio etc/containers \
     etc/sysctl.d etc/modules-load.d etc/udev/rules.d etc/yum.repos.d \
     etc/chrony.conf etc/hosts etc/fstab etc/default/grub etc/sudoers.d \
     root/.ssh/authorized_keys' > ~/master1-swap-2026-11/etc-swapday.tgz
   ```

   Facts the capture pins down: SELinux **disabled**, firewalld
   **disabled**, no swap, all interfaces **DHCP** (addresses come from
   MAC reservations, so they return unchanged), kernel arg
   `initcall_blacklist=simpledrm_platform_driver_init`, repos
   `isv_cri-o_stable_v1.36` + `kubernetes-1.36`, packages
   `cri-o-1.36.3`, `kubelet/kubeadm/kubectl-1.36.4`,
   `iscsi-initiator-utils`, `nfs-utils`, `cryptsetup`, `chrony`.
   `/var/lib/longhorn` is already mounted by stable path
   (`/dev/disk/by-id/nvme-WD_Blue_SN570_2TB_23172Q802011_1-part2`).

### Phase 1 — Preflight gates (T-30 min)

Stop if any fails.

Define an etcd helper first (a function, so it also works in zsh, which
does not word-split a `$E`-style variable):

```sh
etcdctl2() {
  kubectl -n kube-system exec etcd-master2.${SECRET_DOMAIN} -- etcdctl \
    --endpoints=https://127.0.0.1:2379 --cacert=/etc/kubernetes/pki/etcd/ca.crt \
    --cert=/etc/kubernetes/pki/etcd/peer.crt --key=/etc/kubernetes/pki/etcd/peer.key "$@"
}
```

```sh
kubectl get nodes                                   # all Ready
etcdctl2 endpoint status --cluster -w table         # 3 members, one leader, no errors
for m in master1 master2 master3; do
  echo "$m $(kubectl -n kube-system logs etcd-$m.${SECRET_DOMAIN} --since=1h | grep -c 'took too long')"
done
kubectl rook-ceph ceph status                       # HEALTH_OK, all PGs active+clean
kubectl -n longhorn-system get volumes.longhorn.io  # nothing degraded / rebuilding
flux get ks -A --status-selector ready=false        # empty
```

The "took too long" counts are **never** near zero on this cluster.
Baseline (2026-11-21, 1 h): master1 ~3.9k, master2 ~2.1k, master3 ~0.7k,
with multi-second tails on the two VMs. Proceed if master2/master3 are
within ~2x of that baseline and `endpoint status` shows no errors or recent
leader change; postpone if either VM is far above it — they carry etcd
alone during the window.

No Longhorn volume has its only replica on master1, and no single-instance
CNPG cluster runs there (checked 2026-11-21) — re-check:

```sh
# which CNPG primaries sit on master1
kubectl get clusters.postgresql.cnpg.io -A \
  -o jsonpath='{range .items[*]}{.metadata.namespace} {.status.currentPrimary}{"\n"}{end}' |
  while read ns pod; do
    [ "$(kubectl -n "$ns" get pod "$pod" -o jsonpath='{.spec.nodeName}')" = "master1.${SECRET_DOMAIN}" ] && echo "$ns/$pod"
  done
```

### Phase 2 — Snapshot and storage prep

1. **etcd snapshot** from a surviving voter, copied off-host:

   ```sh
   kubectl -n kube-system exec etcd-master2.${SECRET_DOMAIN} -- etcdctl \
     --endpoints=https://127.0.0.1:2379 \
     --cacert=/etc/kubernetes/pki/etcd/ca.crt \
     --cert=/etc/kubernetes/pki/etcd/peer.crt \
     --key=/etc/kubernetes/pki/etcd/peer.key \
     snapshot save /var/lib/etcd/pre-master1-swap.db
   scp root@master2:/var/lib/etcd/pre-master1-swap.db ~/master1-swap-2026-11/
   ```

2. **Ceph `noout`** — osd.1 lives on the WD Blue, which stays in the box,
   so keep Ceph from rebalancing ~2 TB while master1 is down (Rook's own
   drain handling only holds `noout` for 30 min):

   ```sh
   kubectl rook-ceph ceph osd set noout
   ```

3. **Move CNPG primaries off master1** (controlled switchover instead of a
   drain-time failover). For each primary on master1, promote a replica on
   another node:

   ```sh
   kubectl cnpg status -n databases <cluster>      # pick a healthy replica
   kubectl cnpg promote -n databases <cluster> <replica-instance>
   ```

### Phase 3 — Take master1 out of the cluster

```sh
kubectl cordon master1.${SECRET_DOMAIN}
kubectl drain master1.${SECRET_DOMAIN} --ignore-daemonsets \
  --delete-emptydir-data --force --timeout=15m
```

Remove master1's etcd member from a survivor (do **not** `kubeadm reset`
master1 — its old drive is kept untouched as a forensic copy):

```sh
etcdctl2 member list -w table                 # note master1's member ID
etcdctl2 member remove <master1-member-id>
etcdctl2 endpoint status --cluster -w table    # 2 members, a leader
kubectl delete node master1.${SECRET_DOMAIN}
ssh root@master1 poweroff
```

kube-vip moves the VIP to a survivor within ~17 s if master1 held it.

### Phase 4 — Hardware

1. Remove the **HS1TBNVME** (read the label — serial `110101000500`).
   **Leave the WD Blue SN570 in its slot, untouched** (Ceph OSD + Longhorn).
2. Fit the 990 PRO (no heatsink) in the freed slot.
3. Label the old drive **"master1 old root — DO NOT BOOT ON THE NETWORK"**:
   booted, it would re-register as `master1` with a removed etcd member.
   Keep it ~2 weeks as a forensic copy, then wipe.

### Phase 5 — Install CentOS Stream 10

1. In the installer, select **only the Samsung 990 PRO** as the install
   target. **Deselect the WD Blue** — selecting it wipes the Ceph OSD and
   Longhorn.
2. Automatic LVM partitioning is fine; **delete the swap LV** (or don't
   create one). Hostname `master1.${SECRET_DOMAIN}`. Network: DHCP on the
   onboard NIC (the reservation returns the same address).
3. After first boot, restore from the capture (extract to a temp dir and
   copy the specific files — don't blindly overwrite `/etc`):
   - `etc/NetworkManager/system-connections/vlan-*.nmconnection` →
     `/etc/NetworkManager/system-connections/` (mode 600), then
     `nmcli con reload && nmcli con up vlan-enp0s31f6.20 && nmcli con up vlan-enp0s31f6.40`
   - `etc/yum.repos.d/isv_cri-o_stable_v1.36.repo`, `kubernetes-1.36.repo`
   - `etc/containers/registries.conf` + `registries.conf.d/`,
     `etc/crio/crio.conf.d/10-crio.conf`
   - `etc/sysctl.d/*`, `etc/udev/rules.d/*rtl*`, `etc/chrony.conf`,
     `root/.ssh/authorized_keys`
   - the `/var/lib/longhorn` line from the captured `etc/fstab`
4. Host settings:

   ```sh
   sed -i 's/^SELINUX=.*/SELINUX=disabled/' /etc/selinux/config
   systemctl disable --now firewalld
   grubby --update-kernel=ALL --args="initcall_blacklist=simpledrm_platform_driver_init"
   dnf install -y cri-o-1.36.3 kubelet-1.36.4 kubeadm-1.36.4 kubectl-1.36.4 \
     iscsi-initiator-utils nfs-utils cryptsetup chrony
   systemctl enable --now crio iscsid chronyd
   systemctl enable kubelet
   mkdir -p /var/lib/longhorn && mount -a && ls /var/lib/longhorn/longhorn-disk.cfg
   sysctl --system
   reboot
   ```

   Compare `rpm -qa --qf '%{NAME}\n' | sort` against the captured
   `rpm-names.txt` for anything else that matters (e.g. `nvme-cli`).
5. **Prove the fix before joining** — etcd's own disk test on the new drive
   (the 99th-percentile `fdatasync` should be **< 10 ms**; the old drive ran
   ~30 ms):

   ```sh
   dnf install -y fio && mkdir -p /var/lib/etcd-fio
   fio --name=etcd --directory=/var/lib/etcd-fio --rw=write --ioengine=sync \
     --fdatasync=1 --size=22m --bs=2300
   ```

### Phase 6 — Rejoin as a control plane

1. Join material from a survivor:

   ```sh
   CERT=$(ssh root@master2 'kubeadm init phase upload-certs --upload-certs' | tail -1)
   JOIN=$(ssh root@master2 'kubeadm token create --print-join-command')
   ssh root@master1 "$JOIN --control-plane --certificate-key $CERT"
   ```

   If `upload-certs` wants a config, render `init/clusterconfiguration.yaml`
   with `envsubst` and pass `--config`, as `init/create-cluster.sh` does.
   The joining node builds its control-plane manifests from the
   `kubeadm-config` ConfigMap, so cluster-wide settings (e.g. the
   apiserver `GOMEMLIMIT`) come back automatically.
2. **Do not add a kube-vip static pod.** kube-vip is a DaemonSet on
   control-plane nodes; it schedules onto master1 by itself.
3. master1 is a *schedulable* control plane — remove the taint and restore
   the one manual label (NFD / the Intel GPU plugin re-publish the rest):

   ```sh
   kubectl taint nodes master1.${SECRET_DOMAIN} node-role.kubernetes.io/control-plane:NoSchedule-
   kubectl label nodes master1.${SECRET_DOMAIN} node.longhorn.io/create-default-disk=true
   ```

4. Check: `etcdctl2 member list` shows 3 started members; `kubectl get csr`
   has nothing stuck Pending.

### Phase 7 — Storage back in service

```sh
kubectl -n rook-ceph get pods -o wide | grep -E 'osd-prepare|osd-1'   # osd.1 re-activated on master1
kubectl rook-ceph ceph osd tree                                      # osd.1 up/in
kubectl rook-ceph ceph osd unset noout
kubectl rook-ceph ceph status                                        # HEALTH_OK, active+clean
kubectl -n longhorn-system get nodes.longhorn.io master1.${SECRET_DOMAIN}   # Ready, disk schedulable
kubectl get clusters.postgresql.cnpg.io -A                           # all healthy
```

### Phase 8 — Verify the fix

- master1 etcd WAL fsync p99 **< 10 ms** (was ~30 ms); backend-commit p99
  in line with master2/3; `etcd_server_slow_read_indexes_total` on master1
  growing at the peers' rate.
- No kube-vip restarts; `KubeVipLeaderChurn` quiet.
- RTL-SDR receiver pod back on master1; `flux get ks -A` clean.

### Rollback

- **Before the etcd `member remove`:** `kubectl uncordon master1` — done.
- **After it:** keep going forward. The cluster is fine on 2 voters for
  hours (the beast SPOF is the only exposure), so a failed install or join
  can simply be retried. Don't put the old drive back: its etcd member is
  gone and it would re-register a conflicting `master1`.
- **Worst case** (etcd lost): restore from the Phase 2 snapshot per
  [Cluster Rebuild](cluster_rebuild.md).

## Sequencing constraints

Before doing either replacement option, confirm:

1. **Cluster is otherwise healthy.** Same gating conditions as the
   [Promote Worker to Control Plane](promote_worker_to_control_plane.md)
   preconditions — etcd voter health, Ceph HEALTH_OK, no in-flight
   long storage operations.
2. **Etcd snapshot saved off-host.** See the Etcd snapshot section of
   the same runbook.
3. **Immich offsite seed finished 2026-05-05** (5-file sample
   round-trip verified the same day). Paperless seed also finished
   2026-05-05 (778 MiB across docs + DB). This gating constraint is
   resolved — the drive swap no longer needs to coordinate around the
   initial seed. See [`offsite_recovery.md`](offsite_recovery.md).

## After master1 is fixed

In order:

1. **Verify the fix landed.** master1's etcd `slow_apply_total`
   should grow at the same rate as the other voters over a 10-minute
   window. If yes, the disk swap solved the etcd problem.
2. **Investigate `BLUESTORE_SLOW_OP_ALERT` on OSD-1.** Separate
   issue, on the WD Blue SN570. Likely Longhorn ↔ Ceph contention on the same
   drive. Quantify with `iostat` and Ceph perf counters before
   deciding what to do.
3. **Then promote worker2 as a control plane** per
   [Promote Worker to Control Plane](promote_worker_to_control_plane.md),
   retiring the VM master2.
4. **Then repeat for master3** with another bare-metal worker, so all
   three control planes live in independent failure domains.

## Follow-ups

- **UPS clean shutdown for master1.** The 990 PRO has no power-loss
  protection, so a hard power cut mid-write is the remaining etcd risk
  (no worse than the old drive). No host in the cluster runs NUT today;
  master1 can run `nut` with the `snmp-ups` driver against its UPS's
  network card and power off cleanly on low battery.
- **Multi-second applies on the VM masters.** On 2026-11-21 master2/master3
  showed slow-apply p99 of 4.7 s / 7.3 s (max ~8 s) in one hour, despite
  WAL fsync p99 ~25 ms, negligible CPU steal and ~24% free memory — likely
  large read transactions, not disk. Worth a look after the swap: they are
  2 of the 3 voters, and both live on beast.
