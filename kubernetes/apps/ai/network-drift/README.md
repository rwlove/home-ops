# network-drift — continuous NetBox-freshness sweep

NetBox is an **observer / mirror** of the live network. This app reconciles
NetBox *from* live state; it never pushes NetBox → devices, and it never touches
the DHCP critical path. Two loops:

- **Loop A (event-driven write-back)** — when the net operator mutates live state
  (an Omada port, a DNS/DHCP record via the netop foothold, a NetBox record), it
  writes NetBox in the *same* transaction. Immediate, intent-aware. *(Phase 3.)*
- **Loop B (scheduled discovery sweep)** — this CronJob. Pulls live reality, diffs
  NetBox, catches out-of-band changes and any missed Loop-A write. *(Phase 1–2.)*

## What runs today (Phase 1)

A **deterministic, stdlib-only Python detector** (`resources/netbox_sync.py` +
`mcp_http.py`) — **zero LLM tokens**, so it runs **daily**. It:

1. Reads live Omada and NetBox through the **read-only MCP services**
   (`omada-mcp` `all:r`, `netbox-mcp`) over plain JSON-RPC — never the
   mcp-gateway broker, never a device.
2. Diffs deterministically. Comparators:
   - **Phase 1** — Omada ↔ NetBox: VLANs, prefixes, infra devices.
   - **Phase 2** — brain **dhcpd** reservations/subnets (via github-mcp) ↔ NetBox
     ip-addresses/prefixes; **DNS** forward-zone names ↔ NetBox `dns_name`
     (freshness); **k8s** nodes + **LoadBalancer** Service IPs + **Cilium LB
     pools** (via a scoped read-only ServiceAccount) ↔ NetBox. All
     one-directional (reality → record) except the dns_name freshness check.
3. Emails Rob via `notify-mcp /send` **only when the drift picture CHANGES** —
   new drift appears or known drift resolves. A healthy or unchanged network
   sends nothing.

It is **detect-only**: it cannot write NetBox, Omada, or any device.

### State-change awareness + ack list

- The previous run's drift fingerprints live on the `network-drift-state`
  ceph-block PVC (`/state/seen.json`). Only the *delta* emails.
- To permanently silence a known/planned item, paste its 12-char `fingerprint`
  (shown in the digest) into [`app/resources/acked.txt`](app/resources/acked.txt)
  and merge. Git-native ack store — remove the line to un-ack.

## Operating

```sh
# Manual test (ships suspended):
kubectl -n ai create job --from=cronjob/network-drift network-drift-test
kubectl -n ai logs job/network-drift-test        # look for NETBOX_SYNC_RESULT=…
# Then flip suspend:false in app/cronjob.yaml.
```

Tool names / args and MCP endpoints are config (`resources/mcp.json`, constants
at the top of `netbox_sync.py`) — a correction is a ConfigMap edit, not an image
rebuild.

## Roadmap

| Phase | Adds | Writes? |
|---|---|---|
| **1** | deterministic Omada↔NetBox detector, daily, state-change digest | no |
| **2** | collectors for dhcpd reservations/subnets, DNS names, Cilium LB pools, k8s nodes | no |
| **3** | Loop A write-back wiring (the net op's own write paths update NetBox same-transaction) | — |
| **4** | gated deterministic upsert (NetBox Diode) + gated LLM/kagent residual, via the agent-ops queue | **ships OFF** |

Phase 4's auto-write is **off by default** and stays Rob's one-line decision after
Phases 1–2 produce real drift-classification evidence.
