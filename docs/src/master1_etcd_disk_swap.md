# master1 etcd-Disk Swap Plan

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
suspected drive is `nvme1n1` — a budget HS1TBNVME M2 1TB at 41% wear,
hosting the LVM root that contains `/var/lib/etcd`.

Cluster has a leader, zero leader changes/hr, no failed proposals —
etcd is stable but degraded. Apiserver-master1 takes the brunt
because it always reads from local etcd.

Unrelated note: `/dev/sdb` on master1 is in `XfsShutdown` state — a
secondary SATA disk that fell off the bus. **Does not affect etcd**
(verified: nothing on sdb is mounted; etcd lives on `/`, OSD-1 lives
on `nvme0n1p1`, Longhorn lives on `nvme0n1p2`). Investigate on its
own schedule.

## Drive recommendation

**Micron 7450 PRO 480 GB M.2 2280** (MTFDKBA480TFR-1BC1ZABYYR), ~$200 USD.

Why this one:
- M.2 2280 fits the most common slot (verify before ordering).
- 480 GB is plenty for `/` + etcd (etcd DB is ~340 MB).
- 1 DWPD, 800 TBW endurance — etcd will not wear it out.
- Hardware power-loss-protection capacitors — the whole point.
- Gen4×4, negotiates down to Gen3.

Alternatives if master1 has an M.2 22110 slot (longer 110 mm form factor):
- Samsung PM9A3 960 GB M.2 22110 (~$230–280)
- Micron 7450 PRO 960 GB M.2 22110 (~$270–370)

See conversation history for retailer links; pricing as of 2026-05-04.

## Preflight (before ordering)

1. **Confirm M.2 slot length on master1**: SSH and run `lspci -vv`,
   or look at the board. 2280 vs 22110 changes the buy.
2. **Confirm where `cs_master1-root` LVM actually sits**:
   ```sh
   pvs && vgs && lvs && lsblk
   ```
   This is to verify the hypothesis that `/var/lib/etcd` is on
   `nvme1n1`. If it turns out to be on `nvme0n1` (the WD Blue), the
   diagnosis changes — the issue would be NVMe contention with
   Longhorn/Ceph rather than a slow drive. The fix would then be
   different (move workloads, not the drive).
3. **Identify what was on `/dev/sdb`**:
   ```sh
   cat /etc/fstab | grep sdb
   blkid /dev/sdb              # may return nothing if device gone
   dmesg -T | grep -E 'sd[ab]|ata|sata'
   ```
   This is independent of the etcd fix but should be cleaned up
   eventually.

## Replacement options

Two paths once the drive arrives:

### Option A — Reinstall fresh (recommended)

Treat master1 as a control-plane replacement: drain, remove from
cluster, reinstall the OS on the new NVMe, rejoin via `kubeadm join
--control-plane`. Symmetrical to
[Promote Worker to Control Plane](promote_worker_to_control_plane.md)
— the same Phase 2/3/4 apply, just with master1 instead of worker2.

Pros: clean state, drops accumulated cruft, validates the
documented procedure.

Cons: more downtime, more steps.

### Option B — Clone LVM offline

Boot master1 into rescue/live media, `dd` or `pvmove` the existing
root LVM from `nvme1n1` to the new drive, update bootloader, reboot.

Pros: faster, preserves config exactly.

Cons: carries forward whatever cruft is on `/`; bootloader fiddling;
no validation that the cluster's join procedure works.

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
   issue, on `nvme0n1`. Likely Longhorn ↔ Ceph contention on the same
   drive. Quantify with `iostat` and Ceph perf counters before
   deciding what to do.
3. **Then promote worker2 as a control plane** per
   [Promote Worker to Control Plane](promote_worker_to_control_plane.md),
   retiring the VM master2.
4. **Then repeat for master3** with another bare-metal worker, so all
   three control planes live in independent failure domains.
