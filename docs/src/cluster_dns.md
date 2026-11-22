# Cluster DNS

How in-cluster name resolution works, and why it is configured the way it is.
Source of truth: `kubernetes/apps/kube-system/coredns/app/helmrelease.yaml`.

## Where a pod's resolver config comes from

The kubelet writes each pod's `/etc/resolv.conf`:

| Line | Source |
|---|---|
| `nameserver 10.43.0.10` | kubelet `clusterDNS` (the 10th IP of the service subnet in `init/clusterconfiguration.yaml`) |
| `search <ns>.svc.cluster.local svc.cluster.local cluster.local` | kubelet `clusterDomain` (`dnsDomain` in the same file) |
| `search … <lan domain>` | the node's own `/etc/resolv.conf`, written from the LAN gateway's DHCP leases |
| `options ndots:5` | the Kubernetes default |

The LAN domain stays in pod search paths on purpose: some apps resolve
bare LAN hostnames through it.

## CoreDNS server blocks

CoreDNS answers from three server blocks; the most specific zone wins.

1. **`cluster.local.` + reverse zones**: the `kubernetes` plugin. Missing
   names return NXDOMAIN; a Service's AAAA is NOERROR with no records.
2. **`svc.` / `cluster.` / `pod.`**: names under these made-up top-level
   domains exist only in-cluster. They are answered NXDOMAIN locally, so a
   failed or mistyped cluster name is never forwarded off the cluster.
3. **`.` (everything else)**:
   - **AAAA is answered NOERROR with no records, locally.** The cluster is
     IPv4-only (Cilium `enable-ipv6: false`), so no IPv6 lookup ever leaves
     the cluster.
   - **A is forwarded** to the LAN gateway's DNS (authoritative for the LAN
     zone), with the LAN Pi-hole as a fallback (`policy sequential`). The
     upstreams are explicit, not inherited from each control-plane node's
     DHCP-written `/etc/resolv.conf`.
   - **The fallback covers internet names only.** The Pi-hole answers the
     LAN zone by forwarding back to the gateway, so while the gateway's DNS
     is down, external lookups keep working but LAN names and LAN reverse
     lookups fail. CoreDNS fails over on connection errors and failed health
     checks, not on a SERVFAIL from a gateway that is still reachable.

## Where CoreDNS runs

CoreDNS runs on **bare-metal nodes only** (`feature.node.kubernetes.io/virtual-machine: "false"`,
set from the CPUID hypervisor flag by an NFD rule), spread one replica per node.
Cluster DNS timeouts were traced to UDP loss that tracks CPU load on the hypervisor
hosting the VM nodes. All three replicas used to run in those VMs, which could lose
20-40% of their CPU to hypervisor steal. Switch port counters were ruled out as the
main cause.

## Why `ndots` is not tuned

With `ndots:5`, a name with fewer than 5 dots is tried with every search
suffix before being tried as written. A full name such as
`x.ns.svc.cluster.local` (4 dots) therefore costs several NXDOMAIN lookups
first. Lowering `ndots` would remove those, but **five namespaces (`ai`,
`media`, `network`, `storage`, `tools`) are real internet top-level domains**.
With `ndots:2`, a relative name like `<pod>.<svc>.media` would be asked of
the internet *before* the cluster, and a registered internet name would win.

Instead:

- **In-cluster names in config carry a trailing dot** where the hostname is
  only used to connect (database, Redis, SMTP and MQTT hosts), so they are
  queried exactly once. Not for `http(s)://` URLs: the dot would end up in
  the `Host` header, the TLS server name and S3 request signing.
- **The extra search-suffix lookups for external names** are answered
  inside the cluster or the LAN, never by the internet.
- **If you change a host in an app UI to the dotted form**, also change any
  setting keyed on that exact host string. Example: a media library
  manager's Remote Path Mapping must match its download client's host text,
  or imports stall.

## Finding which workloads cause extra lookups

`hubble_dns_queries_total` (Cilium Hubble metrics) carries the query name,
`source_namespace`, `source_pod` and `ips_returned`. It covers A queries
from pods whose traffic passes through the Cilium DNS proxy. A search-suffix
miss is a query that returned no IPs and ends in a search suffix:

```promql
topk(15, sum by (source_namespace, workload) (
  label_replace(
    rate(hubble_dns_queries_total{ips_returned="0",
      query=~".+\\..+\\.(svc\\.cluster\\.local|cluster\\.local|<lan-domain>)\\."}[1h]),
    "workload", "$1", "source_pod", "(.+?)(-[a-z0-9]{8,10})?(-[a-z0-9]{5}|-[0-9]+)?$")
) * 3600)
```

Cluster-wide context:

- `sum(increase(coredns_dns_responses_total{rcode="NXDOMAIN"}[1h]))`: all
  NXDOMAIN answers.
- `sum(increase(coredns_proxy_request_duration_seconds_count[1h]))`: queries
  CoreDNS forwarded off the cluster.
- `sum(increase(cilium_proxy_upstream_reply_seconds_count{protocol_l7="dns",error="timeout"}[1h]))`:
  DNS-proxy timeouts, which cause the 5 s client stalls.

To see the actual names, run
`hubble observe --protocol dns --type l7 --since 10m -o json` in each
`cilium-agent` pod and group the NXDOMAIN responses by destination pod.
