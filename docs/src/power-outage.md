# Whole-home power outage / cold-start recovery

After a full power outage where every node power-cycled at once, `kube-vip` may fail to come up cleanly — its static-pod manifest depends on the control-plane VIP (`192.168.6.1`) being reachable, but the VIP itself is owned by `kube-vip`. Chicken-and-egg.

The fix: manually attach the VIP to `master1`'s physical interface so the apiserver becomes reachable. Once apiserver is up, `kube-vip` reconciles and re-takes ownership of the VIP automatically.

## Recovery

SSH into `master1` and add the VIP to the primary interface:

```sh
ssh root@master1.${SECRET_DOMAIN}
ip addr add 192.168.6.1/32 dev $(ip -br -4 route show default | awk '{print $5}')
```

The interface auto-detection picks whatever holds the default route — currently `enp0s31f6` on master1, but the detection makes this resilient to NIC swaps and OS rebuilds.

Confirm it stuck:

```sh
ip -br -4 addr show | grep 192.168.6.1
curl -k https://192.168.6.1:6443/healthz   # apiserver responds
```

Within ~60 seconds, `kube-vip` pods should come up and you can delete the temporary VIP — or just leave it; `ip addr add` is non-persistent and disappears on the next reboot.

## Why it happens

- `kube-vip` runs as a static pod on each control-plane node, managed by kubelet, not by the apiserver.
- It uses [BGP ECMP via Cilium](../../kubernetes/apps/network/cilium/) in this cluster, but during cold boot the VIP attach can race against kubelet bringing up the static pod.
- Once the VIP is up *anywhere*, every node's kubelet can talk to the apiserver and the rest cascades.

## Related

- [Cluster Rebuild](cluster_rebuild.md) — for the case where the cluster genuinely needs to be reinitialized rather than just nudged.
