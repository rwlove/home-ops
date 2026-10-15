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

## Loop A write-back (Phase 3)

`resources/netbox_writeback.py` (library) + `resources/writeback_cli.py` (CLI)
are the **programmatic reconcile-on-write** complement to the discipline doc.
After the operator mutates live state, they run one call to mirror it into NetBox
in the same change — better than waiting for the nightly Loop B sweep to infer
intent:

```sh
# after `netop-dns add-host foo 10.10.30.9`:
writeback_cli.py ip-upsert 10.10.30.9/24 --dns-name foo.thesteamedcrab.com --apply
# after `netop-dns remove-host foo`:
writeback_cli.py ip-delete 10.10.30.9/24 --apply
# after an Omada network/VLAN change:
writeback_cli.py vlan-upsert 30 IoT --apply
```

- **Dry-run is the default** — nothing is written without `--apply`. Shipping this
  code writes nothing.
- Records it creates/updates are tagged **`netbox-sync`** (`OWNED_TAG`), which is
  what lets Phase 4 scope a write-token / auto-write to only the objects the sync
  owns.
- Reads `NETBOX_URL` / `NETBOX_TOKEN` from the operator's environment at call
  time; the module never bakes or logs a token.
- **Still needs (operator / cross-repo):** calling `writeback_cli.py … --apply`
  from the brain netop foothold wrappers and the Omada OpenAPI write recipe (both
  live outside home-ops) so the mirror happens automatically; and a NetBox token
  with write on `ipam` ip-addresses/prefixes/vlans.

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

## Phase 4 — gated auto-write (SHIPS OFF)

`resources/autowrite.py` + `cronjob-upsert.yaml` can, once enabled, deterministically
upsert the **narrow SAFE residual** of drift into NetBox. It is **OFF by default
three independent ways** — merging this app writes **nothing** to NetBox:

1. **`cronjob-upsert.yaml` ships `suspend: true`** — it never runs.
2. **`AUTOWRITE_APPLY=false`** (env) — even if it runs, it is a DRY-RUN that only
   logs/emails what it *would* upsert. **This env is THE toggle that enables
   auto-write; it defaults OFF.**
3. The **scoped write-token** `ExternalSecret` (`externalsecret-write.yaml`) is
   `NotReady` until Rob provisions the 1Password property, so the Job can't start.

**SAFE set (the only categories ever auto-written):** `netbox-dns-name-stale`
(PATCH `dns_name` on an existing ip-address) and `lb-ip-missing-in-netbox`
(create a LoadBalancer ip-address). Never VLAN renames, prefix edits, device
creation, or anything in the mgmt LAN (`192.168.x`) or k8s ranges
(`10.42/43/45`) — the recovery-path floor, enforced in code.

**Upsert layer:** the design's intended layer is **NetBox Diode** (`UPSERT_BACKEND=diode`,
avoids brittle GET→diff→PATCH); until its server is deployed the working backend is
the Phase 3 write-back library (`UPSERT_BACKEND=writeback`, the default).

**Internal-consistency validation:** `resources/netbox_report_consistency.py` is a
read-only **NetBox Script** (dup IPs, orphan prefixes/VLANs, empty dns_name).
Install it inside NetBox (`SCRIPTS_ROOT` / Operations → Scripts); it is not wired
to any cluster workload.

**Gated LLM/kagent residual** (`LLM_RESIDUAL_ENABLED=false`): a documented seam to
route the non-safe residual to the kagent network-operator for classification via
the agent-ops queue — off, not wired to a model in this phase.

### Enabling Phase 4 (Rob, after Phase 1–2 evidence)

1. Provision the scoped write token (see `externalsecret-write.yaml` header:
   NetBox object-permissions → write on `ipam` ip-address/prefix/vlan only).
2. `kubectl -n ai create job --from=cronjob/network-drift-upsert nbw-test` and
   read the DRY-RUN email/logs (`AUTOWRITE_RESULT=…`).
3. Flip **`AUTOWRITE_APPLY: "true"`** in `cronjob-upsert.yaml` and `suspend: false`.

## Roadmap

| Phase | Adds | Writes? |
|---|---|---|
| **1** | deterministic Omada↔NetBox detector, daily, state-change digest | no |
| **2** | collectors for dhcpd reservations/subnets, DNS names, Cilium LB pools, k8s nodes | no |
| **3** | Loop A write-back library + CLI (operator's write paths mirror NetBox same-transaction) | dry-run default |
| **4** | gated SAFE-residual upsert (write-back lib now, NetBox Diode later) + NetBox Reports validator + gated LLM/kagent residual | **ships OFF** |

Phase 4's auto-write is **off by default** (suspended + `AUTOWRITE_APPLY=false` +
NotReady write-token) and stays Rob's decision after Phases 1–2 produce real
drift-classification evidence. The single enabling toggle is `AUTOWRITE_APPLY`.
