# Kubelet Tuning

The cluster-wide kubelet settings, why each one is set, and how to roll a
change out to running nodes.

## Where the settings live

| Stage | Source of truth |
|---|---|
| New cluster (`kubeadm init`) | The `KubeletConfiguration` document in `init/clusterconfiguration.yaml` |
| Running cluster | The kubeadm-managed `kube-system/kubelet-config` ConfigMap |
| Per node | `/var/lib/kubelet/config.yaml`, rendered from the ConfigMap |

The ConfigMap is not Flux-reconciled. Editing it changes nothing until each
node re-syncs. Keep `init/clusterconfiguration.yaml` in step with it, or a
rebuild silently drops the tuning.

## Current non-default settings

| Setting | Value | Why |
|---|---|---|
| `imageGCHighThresholdPercent` / `imageGCLowThresholdPercent` | 72 / 62 | Disk-pressure backstop (see [failure mode runbooks](failure_mode_runbooks.md)) |
| `imageMaximumGCAge` | 48h | Removes dangling image digests that threshold GC never reaches |
| `serializeImagePulls` / `maxParallelImagePulls` | false / 3 | With serial pulls, one multi-GB image blocked every other pod start on the node (p99 pull 440–1593s on several workers) |
| `shutdownGracePeriod` / `shutdownGracePeriodCriticalPods` | 120s / 30s | Graceful node shutdown: regular pods get 90s, then critical pods 30s, on reboot or UPS poweroff |
| `systemReserved` / `kubeReserved` | 1Gi + 100m each | Keeps pod requests from claiming the OS's and kubelet's memory; allocatable is 2.4Gi lower per node |
| `evictionHard.memory.available` | 500Mi (was 100Mi) | The kubelet evicts pods before the kernel OOM killer has to step in. Every signal is listed, because setting the map replaces the defaults |

Applied fleet-wide on 2026-11-22. Before the reservations, 13 CNPG clusters
had their memory requests lowered (#16341, #16343, #16345) so every node's
requests fit the smaller allocatable.

### Graceful node shutdown

The kubelet writes `/etc/systemd/logind.conf.d/99-kubelet.conf`
(`InhibitDelayMaxSec=120`) and takes a `delay` inhibitor lock on
`shutdown`. Verify on a node:

```sh
busctl get-property org.freedesktop.login1 /org/freedesktop/login1 \
  org.freedesktop.login1.Manager InhibitDelayMaxUSec   # t 120000000
systemd-inhibit --list | grep kubelet
```

This only helps a shutdown that goes through systemd (`systemctl
poweroff`/`reboot`, an ACPI power button, a UPS client calling
`shutdown`). A hard power cut still kills everything. Pods stopped
this way are left behind as `Failed` with reason `Terminated`. Their
controllers replace them, and the pod GC removes the leftovers.

## Rolling a change out

1. Back up the ConfigMap:
   `kubectl -n kube-system get cm kubelet-config -o yaml > kubelet-config.backup.yaml`
2. Edit the `kubelet` key of the ConfigMap. Mirror the change in
   `init/clusterconfiguration.yaml`.
3. Re-sync one node at a time. A canary worker with no Ceph mon goes first,
   mon-hosting workers go last, then the masters one at a time:

   ```sh
   ssh root@<node> "cp /var/lib/kubelet/config.yaml /var/lib/kubelet/config.yaml.bak && \
     kubeadm upgrade node phase kubelet-config && systemctl restart kubelet"
   ```

   Restarting the kubelet does not touch running containers. CRI-O and
   containerd are separate daemons, and static pods (etcd, apiserver)
   keep running.
4. Verify each node before moving on:

   ```sh
   kubectl get node <node>    # Ready
   kubectl get --raw /api/v1/nodes/<node>/proxy/configz | jq .kubeletconfig
   ```

   Check that the Running pod count is unchanged, that the CephCluster
   reports `HEALTH_OK` (after mon hosts), and that
   `kubectl get --raw /readyz/etcd` is `ok` (after masters).

### Trap: shrinking allocatable

Any change that lowers node allocatable is a different class of change:
`systemReserved`, `kubeReserved`, or a higher `evictionHard` memory or
disk threshold. On restart the kubelet re-admits the pods bound to the
node against the new allocatable. If the sum of requests no longer fits,
it can reject running pods (`OutOfmemory` / `OutOfcpu`). Before any such
change, confirm on every node that the sum of memory requests stays below
the new allocatable. Count only Running/Pending pods: kube-state-metrics
also reports the requests of Completed pods (such as a finished CronJob),
which the kubelet ignores at admission:

```promql
sum by (node)(kube_pod_container_resource_requests{resource="memory"}
  * on(namespace,pod) group_left()
    (max by (namespace,pod)(kube_pod_status_phase{phase=~"Running|Pending"}) == 1))
  / sum by (node)(kube_node_status_allocatable{resource="memory"})
```

Check CPU requests the same way; reserving CPU shrinks CPU allocatable too.

### Trap: do not cordon to make room

`kubectl cordon` adds the `node.kubernetes.io/unschedulable` taint. Before
2026-11-22 the descheduler's `RemovePodsViolatingNodeTaints` plugin treated
that taint as a violation and evicted every pod on the node, including CNPG
primaries (five failed over). The taint is now in the plugin's
`excludedTaints`. Even so, to free room on one node, delete the specific
pods you want moved instead of cordoning.
