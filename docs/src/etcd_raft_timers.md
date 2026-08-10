# etcd Raft Timer Tuning

Runbook for `etcdHighNumberOfLeaderChanges`, and the record of the timer
change applied 2026-08-09.

## TL;DR

`heartbeat-interval` was raised 100 → 250 ms and `election-timeout`
1000 → 2500 ms on all three control-plane nodes, because **the leader
could not meet a 100 ms heartbeat deadline** under this cluster's write
load. The alert fires on master1, but master1 is the symptom, not the
cause.

This is mitigation. The underlying constraint is the sync-write latency
of the control-plane disks — see [Where the real problem is](#where-the-real-problem-is).

## Diagnosing it — the query that actually discriminates

Do not start from the node the alert names. Start here:

```promql
increase(etcd_server_heartbeat_send_failures_total[6h])   # leader only
increase(etcd_server_slow_apply_total[6h])
increase(etcd_network_peer_sent_failures_total[6h])
```

Measured 2026-08-09, before the change:

| metric (6 h) | master1 | master2 (leader) | master3 |
|---|---|---|---|
| `heartbeat_send_failures` | 0 | **499** | 0 |
| `slow_apply` | 7 784 | 5 740 | 3 731 |
| `peer_sent_failures` | 0 | 0 | 0 |

`peer_sent_failures` being zero everywhere rules out packet loss — peer
delivery never fails. What fails is the leader meeting its own heartbeat
deadline, on top of every member applying slowly. Followers then reach
the 1000 ms election timeout and campaign.

### Why master1 books the leader changes

master1 had seen **284** leader changes against **24** on each of the
others. That is real, not a counter reset — its etcd had 53 days of
uptime. master1 simply times out first (busiest disk, and the only
control-plane node on a different subnet from the other two), campaigns,
loses to the stable pair, and rejoins — incrementing only its own
counter. The other two never lose their leader, so they never count a
change.

## Traps

Four things that cost real time in this diagnosis:

- **Counters reset on restart.** `etcd_server_leader_changes_seen_total`
  is per-member and resets when etcd restarts. Check
  `process_start_time_seconds` before drawing conclusions from a total,
  and compare *rates* after any rollout.
- **Never judge disk latency from an instant query.** Two failure modes
  bite here. First, the fsync histogram uses power-of-two buckets, so
  `p99` can read identically on every member simply because they all
  land in the same bucket — identical values are *not* evidence the
  disks are equal. Second, a single sample is easily unrepresentative:
  a member that has just restarted looks fast because it is idle. Use a
  median over hours, and cross-check `wal_fsync` against
  `backend_commit`:

  ```promql
  quantile_over_time(0.5,
    (histogram_quantile(0.99,
      sum by (instance,le) (rate(etcd_disk_wal_fsync_duration_seconds_bucket[10m]))))[9h:15m])
  ```

- **The etcd image is distroless.** `kubectl exec … -- env ETCDCTL_API=3
  etcdctl` fails with ``executable file `env` not found``. Call
  `etcdctl` directly; API v3 is the default since etcd 3.4.
- **Counters are readable without Prometheus.** Each member serves
  `http://127.0.0.1:2381/metrics` (`listen-metrics-urls` is already set
  cluster-wide), which is often faster than a port-forward.

## Applying a timer change

Timers **must be identical on every member**; mismatched values cause the
instability you are trying to cure. There is an unavoidable mixed window
during rollout — do all three back to back.

### 1. Patch the cluster config

`init/clusterconfiguration.yaml` is the bootstrap source of truth but is
**not** Flux-reconciled, so editing it changes nothing live. The live
source is the `kubeadm-config` ConfigMap, and `kubeadm upgrade`
re-renders static pod manifests from it — which is why the change must
live there, not in a hand-edited manifest. Keep both in sync.

```bash
kubectl -n kube-system get cm kubeadm-config -o yaml > ~/kubeadm-config.pre.yaml   # rollback
kubectl -n kube-system edit cm kubeadm-config
#   under etcd.local.extraArgs:
#     - name: heartbeat-interval
#       value: "250"
#     - name: election-timeout
#       value: "2500"
```

### 2. Roll the nodes — followers first, leader last

Leader last means exactly one leadership transfer instead of up to three.
Find the leader:

```bash
kubectl -n kube-system exec etcd-<node> -- etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key endpoint status --cluster -w table
```

Then, on each node in turn:

```bash
kubectl --kubeconfig=/etc/kubernetes/admin.conf -n kube-system get cm kubeadm-config \
  -o jsonpath='{.data.ClusterConfiguration}' > /tmp/cc.yaml
cp -a /etc/kubernetes/manifests/etcd.yaml /var/backups/etcd-timers/etcd.yaml.pre   # rollback

# Verify BEFORE applying: the diff must be exactly the flags you added.
kubeadm init phase etcd local --config /tmp/cc.yaml --dry-run 2>/dev/null \
  | sed -n '/^apiVersion: v1/,$p' > /tmp/etcd-new.yaml
diff -u /etc/kubernetes/manifests/etcd.yaml /tmp/etcd-new.yaml

kubeadm init phase etcd local --config /tmp/cc.yaml
```

`kubeadm` reproduces the manifest **byte-for-byte** apart from the flags
you added — verified with `--dry-run` on this cluster. It does not
recompute peer URLs or `initial-cluster`. If a `--dry-run` diff ever
shows more than your intended change, stop: applying it would break that
member.

### 3. Gate on health between nodes

This is the step that protects quorum. All three must report healthy
before touching the next node:

```bash
kubectl -n kube-system exec etcd-<any-running-node> -- etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key endpoint health --cluster
```

Expect a brief blip when the **leader** restarts: `/healthz` may report
`poststarthook/rbac/bootstrap-roles failed` and Flux Kustomizations may
go not-Ready for a few minutes. Both self-resolve; confirm each apiserver
returns `ok` individually before worrying.

## Rollback

Per node — the kubelet reloads the static pod within seconds:

```bash
cp -a /var/backups/etcd-timers/etcd.yaml.pre /etc/kubernetes/manifests/etcd.yaml
```

Roll back **every** node, not just one; a mixed-timer cluster is worse
than either uniform state. Then restore the ConfigMap, or the next
`kubeadm upgrade` will reintroduce the timers:

```bash
# strip resourceVersion/uid/creationTimestamp first, or apply will conflict
kubectl -n kube-system apply -f ~/kubeadm-config.pre.yaml
```

A data-level etcd snapshot is worth taking before any of this:

```bash
kubectl -n kube-system exec etcd-<node> -- etcdctl … snapshot save /var/lib/etcd/snapshot-pre.db
```

Note it lands on the same disk whose latency is under suspicion (~364 MB
at time of writing), and it is the only writable path inside the
distroless container.

## Verifying the change worked

Counters reset on restart, so compare **rates**, not totals. Baselines to
beat, from before the change:

| metric | before |
|---|---|
| `heartbeat_send_failures` | ~83/h on the leader |
| `leader_changes` | ~0.48/h on master1 |
| `slow_apply` | ~1 297/h on master1 |

```promql
increase(etcd_server_heartbeat_send_failures_total[1h])
increase(etcd_server_leader_changes_seen_total[1h])
increase(etcd_server_slow_apply_total[1h])
```

If they do not improve, raft timing was not the binding constraint.

## Where the real problem is

Raising the timers buys headroom; it does not make the disks faster.
master1 remains the slow voter, essentially unchanged since the
2026-05-05 measurements in
[master1 etcd-Disk Swap Plan](master1_etcd_disk_swap.md):

| p99, median over 9 h | master1 | master2 | master3 |
|---|---|---|---|
| `wal_fsync` — May | **31 ms** | 17 ms | 16 ms |
| `wal_fsync` — Aug | **30.5 ms** | 15.6 ms | 15.6 ms |
| `backend_commit` — May | **103 ms** | 29 ms | 27 ms |
| `backend_commit` — Aug | **97 ms** | 24 ms | 26 ms |

master1's sync-write path is roughly **2× slower on fsync and 4× slower
on commit** than the other two voters, and has been for months. etcd
wants `wal_fsync` p99 well under 10 ms; none of the three meet that, but
master1 is the outlier.

So the conclusion of the disk-swap plan stands: **replacing master1's
root NVMe is still the highest-value fix.** Timer tuning is the stopgap
that stops the flapping in the meantime.

> **Measure over a representative window.** An earlier revision of this
> page claimed the disk picture had "inverted" and that master1 was now
> the *fastest* voter. That was wrong. It came from a single 10-minute
> sample taken minutes after the etcd restarts, when master1 had just
> come up idle and the other two were still catching up. A 9-hour median
> at 15-minute resolution shows the opposite, and matches May almost
> exactly. Use `quantile_over_time(0.5, (…)[9h:15m])`, not a bare
> instant query, before concluding anything about disk latency.

## Change log

- **2026-08-09** — `heartbeat-interval` 100 → 250 ms,
  `election-timeout` 1000 → 2500 ms on all three control-plane nodes.
  Rolled followers first, leader last; each node diff-gated with
  `kubeadm --dry-run` and health-gated between nodes. Recorded in
  `init/clusterconfiguration.yaml`.

- **2026-08-10, +10 h** — result measured:

  | metric | before | after 10 h |
  |---|---|---|
  | leader changes | ~11.6/day on master1 | **0** since the rollout |
  | `heartbeat_send_failures` (leader) | ~83/h | **7.6/h** (−91 %) |
  | `slow_apply` | ~1 297/h on master1 | ~1 890/h on master1 |

  `etcdHighNumberOfLeaderChanges` cleared and has not re-fired. The
  flapping stopped outright — the two leader changes master1 shows are
  the rollout's own restarts, and there have been none since.

  `slow_apply` did not improve, which is the expected result: it is
  bound by disk, not by raft timing, and master1's disk is unchanged.
  Note master1 is now the leader, so it is doing more work than it was
  as a follower — the comparison is not like-for-like.
