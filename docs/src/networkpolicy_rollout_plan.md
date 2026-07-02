# NetworkPolicy Rollout Plan

Status: **19 of 21 in-scope namespaces locked down (default-deny enforced). `downloads` + `vpn` carved out due to VXLAN DHCP broadcast incompat with cilium default-deny.**
Owner: home-ops
Last updated: 2026-05-18

> **2026-07-01 update:** the oauth2-proxy fleet this plan references was
> retired (#12767) — auth now rides per-route Envoy Gateway extAuth
> SecurityPolicies against Authelia. Mentions of oauth2-proxy CNPs and
> the proxy → authelia callback chain below are historical. For the
> remaining default-deny flips (`downloads`, `collab`), validate the
> gateway flow instead: unauthenticated request → 302 to the portal,
> login → app loads (no in-namespace proxy hop; envoy-from-network is
> the ingress source to allow).

## Decisions (locked)

1. **`flux-system` and `kube-system` are out of scope.** Neither
   namespace gets a default-deny. Documented in "Out of scope" below.
   Rationale: blast radius of breaking either is loss of the rollback
   mechanism itself; the perimeter (1P/SSO/CF tunnel/firewalld zones)
   already provides the meaningful threat boundary for these namespaces.
2. **One PR per namespace.** Branch `netpol/<ns>`. The
   default-deny ships as a *separate* PR after the allow-policies
   have soaked ≥24h with zero unexplained Hubble drops.
3. **Audit mode first.** Every default-deny lands with
   `policy.cilium.io/audit-mode: enabled` on a single canary pod
   (Phase 1) or the whole namespace (Phase 2+); flip to enforce only
   after Hubble shows zero unexplained `DROPPED-AUDITED` verdicts
   for the per-phase window (48h–2 weeks depending on phase).
4. **`policy-cidr-match-mode` left at default.** All node /
   apiserver flows use entity selectors (`reserved:host`,
   `reserved:remote-node`, `reserved:kube-apiserver`). No `ipBlock`
   for node IPs anywhere.

## Rollout completion summary (2026-05-17)

**Outcome:** 19 of 21 in-scope namespaces have default-deny + per-app
allow CNPs. `flux-system` and `kube-system` remain unconstrained per
Decision #1. `downloads` + `vpn` carved out 2026-05-18 — see
"Downloads + vpn carve-out" below.

| Phase | Namespaces locked down |
|---|---|
| 1 | selfhosted |
| 2 | ai, actions-runner-system, renovate, mcp-system, home, collab, media |
| 3 | auth, databases, vpn, downloads |
| 4 | storage, longhorn-system, rook-ceph |
| 5 | network (v2 — initial attempt reverted due to envoy port mismatch), cert-manager, external-secrets |
| 6 | observability, kuadrant (CNP scaffolded but ns disabled cluster-side; dormant-correct), istio-system, cilium-secrets (special: standalone Flux Kustomization since cilium chart owns the namespace) |

**~35 PRs merged** across baselines, lockdowns, fixes, and follow-ups.

## Downloads + vpn carve-out (2026-05-18)

After the initial lockdown of `downloads` + `vpn` (Phase 3) shipped
and soaked, the gateway-sidecar's init container began crashlooping.
Sequence reconstructed from pod logs and Hubble:

1. Sidecar runs `ping -c 1 10.42.4.4` (gateway pod) → DROPPED under
   default-deny. Fixed by PR #11548 — Pattern E cross-ns ICMP allow
   from `downloads/*` → `vpn/pod-gateway-main`.
2. With ICMP allowed, the VXLAN tunnel comes up successfully.
3. `udhcpc -i vxlan0` sends a DHCP discover broadcast
   (255.255.255.255) encapsulated inside the VXLAN unicast tunnel
   to `vpn/pod-gateway-main-0`.
4. dnsmasq on the gateway side logs DHCPACK going out — but the
   client never sees it. `udhcpc` retries 5×, exits 1, init exits 1,
   container crashloops.

**Root cause:** cilium's security identity for the broadcast frame
inside the VXLAN tunnel doesn't match `vpn/pod-gateway`'s identity,
so the OFFER is silently dropped. This is L2-broadcast-inside-L3-VXLAN,
something cilium's identity model doesn't handle the way it handles
unicast pod-to-pod traffic.

**Decision:** carve out `downloads` + `vpn` from default-deny. PR
\#11555 removed the `default-deny` component from both
`kubernetes/apps/downloads/kustomization.yaml` and
`kubernetes/apps/vpn/kustomization.yaml`. The `baseline` component
(allow-dns/apiserver/intra-ns/monitoring) stays. The per-app `allow-*`
CNPs stay (so cross-namespace access still requires explicit allow
rules at the consumer side). What's lost is the deny-by-default
posture inside these two namespaces.

**Revisit triggers:**

- cilium ships better broadcast-identity handling (track upstream)
- we replace pod-gateway with a static-IP wireguard pattern that
  doesn't need DHCP (eliminates the issue entirely)
- a different VPN egress model that uses node-level routing instead
  of a sidecar (also eliminates the issue)

**Net posture:** 19 of 21 namespaces have full default-deny;
downloads + vpn rely on cluster-perimeter controls (1P/SSO/firewalld,
no public ingress to these workloads, only `bt.${SECRET_DOMAIN}` +
media-pull-stack UIs which are still gated by oauth2-proxy).

## Full rollback 2026-05-18 — reset and redesign

**Outcome:** All default-deny CNPs deleted from the 19 remaining
locked-down namespaces. `cluster-apps` Flux Kustomization SUSPENDED
to persist the rollback before this commit lands. Posture is now
**zero default-deny** cluster-wide. Baseline + per-app allow CNPs
remain in git and in-cluster but have no enforcement effect without
a default-deny.

**Trigger:** User browser-testing on 2026-05-18 surfaced 5+ broken
apps despite every namespace passing audit-mode soak and in-cluster
smoke tests:

- `collab/pump` — OIDC `/oauth2/callback` token exchange failing
  (fixed in PR #11559 ahead of the rollback; the fix shape became
  the canonical pattern for every oauth2-proxy CNP).
- `media/<media-pull-stack apps>`, `media/av1corrector`,
  `media/medialyze`, `media/music-assistant` — same OIDC token
  exchange failure as pump.
- `observability/kube-ops-view`, `observability/goldilocks`,
  `observability/netdata`, `observability/blackbox` — non-oauth
  reachability gaps from various directions (not all root-caused
  yet).

**Why a full reset rather than incremental fixes:** the failures
were not a handful of edge cases — they were a systemic blind spot
in the verification step. The audit-mode soak window cannot exercise
flows that don't happen during the soak (server-side OIDC
`/oauth2/callback` only fires when a user actually completes a
browser login), and in-cluster `curl --resolve` smoke tests bypass
the socket-lb rewrite path that breaks the policy match (see
`project_cilium_l4_port_targetport.md`). Continuing to land
incremental fixes on top of a partially-broken lockdown would
keep extending the user-impact window every time a new app's
OIDC pattern surfaces.

**What was rolled back in git (this commit):**

1. Removed the `network-policy/default-deny` component import from
   all 19 namespace `kustomization.yaml` files that previously had
   it. `baseline` (DNS, apiserver, intra-ns, monitoring scrape,
   host probes) stays — these allows are harmless without a
   default-deny.
2. Corrected the 20 oauth2-proxy CNPs cluster-wide to the
   `envoy:10443` egress pattern (was `toEntities:[world]:443` +
   direct `authelia:9091`, which doesn't match the socket-lb-rewritten
   destination). Mirrors the pump fix (PR #11559) and the storage
   fix (PR #11560). These rules are inert under the current
   no-default-deny posture but will be correct when the next
   re-rollout enables enforcement.

**Re-rollout plan (when work resumes):**

- One namespace at a time, smallest first (start with `selfhosted`).
- After enabling default-deny on a namespace, **user performs real
  external-URL browser verification** (laptop → DNS → cloudflared →
  envoy → backend → oauth2-proxy callback completes → app loads)
  before the next namespace is touched.
- Pattern-A CNPs are no longer accepted as "verified" by `curl URL`
  returning 302 — the full OIDC round-trip must be exercised by a
  human session.
- The audit-mode soak window stays as a discovery mechanism but is
  no longer the *gate* for flipping enforce; the user's browser
  check is.

## Where the testing missed (honest retrospective)

The network-operator agent's prime directive is "you cannot break
the network." It broke things. The user discovered failures rather
than the agent catching them. This is what slipped by:

1. **In-cluster smoke tests gave false confidence.** Lockdown
   agents tested with `curl --resolve` from inside the cluster,
   bypassing the actual external path (laptop → DNS → cloudflared
   → envoy → backend). The initial network-ns lockdown (PR #11524)
   passed all in-cluster checks while every external URL was
   timing out cluster-wide. User noticed first.

2. **Pattern A 302 != working OIDC.** Smoke tests treated "URL
   returns 302" as success. But oauth2-proxy returns 302 to the
   login page even when its server-side token exchange (the call
   from oauth2-proxy pod → authelia after browser-side login) is
   blocked. The actual user-visible failure happens at the
   `/oauth2/callback` step, which isn't exercised by `curl URL`.
   User discovered pump login hanging the morning after collab
   lockdown shipped.

3. **`matchPattern` for canonical FQDNs was assumed to work** based
   on its name. It silently fails for FQDNs without CNAME chains
   (1Password Connect, ACME, pushover, mailgun, auth.${SECRET_DOMAIN}).
   When paperless OIDC surfaced this mid-rollout, the fix was
   applied only to paperless instead of immediately auditing every
   matchPattern allow. Six hours of silent breakage elapsed before
   the user prompted the audit.

4. **Cilium socket-lb rewrite happens BEFORE policy check.**
   In-cluster pods that hit a public hostname via split-horizon
   DNS get their destination rewritten from LB IP to envoy pod IP
   plus targetPort BEFORE BPF L4 policy evaluates. A
   `toEntities: [world]` plus port 443 allow doesn't match the
   rewritten destination (`network/envoy:10443`). Cross-ns
   Pattern E to envoy is also required. Missed during the FQDN
   audit and surfaced when the user reported pump's `/oauth2/`
   URL hanging.

5. **No end-to-end OIDC test gate.** Pattern A overlays were
   verified by curl returning 302. There was no test that
   completed a full OIDC login (curl with cookie jar through the
   full redirect + callback chain). Every oauth2-proxy app in the
   cluster was potentially affected by gaps #2-4 above.

These are not learnings about Cilium — they are testing strategy
gaps in the network-operator agent's verification step. Future
network changes should verify from the user's actual path, not
from an idealized in-cluster path that bypasses the failure mode.

## Key learnings (memorialized to agent memory)

- **Cilium L4 policy evaluates against pod `targetPort`, not Service
  `port`** — `project_cilium_l4_port_targetport.md`. Bit us on
  netbox (svc:80 → pod:8080) and envoy data planes
  (svc:443 → pod:10443, caused the initial network-ns lockdown to
  break cluster-wide ingress; reverted as #11527, redesigned as
  #11529).
- **`toFQDNs.matchPattern` works only for FQDNs with CNAME chains**
  — `project_cilium_matchpattern_fqdn_limits.md`. For canonical
  FQDNs (1Password Connect, ACME endpoints, pushover, mailgun,
  s3.${SECRET_DOMAIN}, etc.) the K8s search-domain augmentation
  makes matchPattern silently fail. Use `toEntities: [world] + port`
  instead. Audited and fixed across 8 namespaces in PRs #11535–11542.
- **`policy-audit-mode: enabled` in cilium-config disables policy
  enforcement entirely WITHOUT capturing audit verdicts** in Cilium
  1.19 with this stack. Don't use as a discovery mechanism. Use
  Hubble flow data on the non-locked baseline as the discovery
  signal instead.
- **`fromEntities: [world, host, remote-node]` on broker pods** is
  the correct shape for LAN-client ingress (EMQX MQTT brokers,
  jellyfin via LB IP, wg-easy VPN). Pure `cluster` doesn't cover
  it because LB IP traffic enters via host identity.
- **Adding a new agent to `rwlove/langgraph-agents` requires updating
  5 hardcoded places** — `project_langgraph_specialist_5_places.md`.
  Caught during the network-operator port (PR #4).
- **Lint must pass on agent-authored PRs** —
  `feedback_lint_must_pass_on_agent_prs.md`. Run `markdownlint-cli2`
  locally before any markdown commit.

## Notable design choices made during rollout

- **Direct-enforce (skip audit window) when canary proves the
  mechanic** — used for selfhosted (1 idle app) and ai (after
  audit-mode discovery proved broken). Tradeoff: missed patterns
  surface as user-impact, rolled back fast via `flux suspend` +
  `kubectl delete cnp`.
- **One PR per namespace** held throughout (Decision #2). Multi-app
  follow-up fixes shipped as separate PRs.
- **Conservative defaults for "tool" or "scan" apps** that fan out
  arbitrarily (renovate scan jobs, GitHub actions runners,
  search-engine scrapers like searxng, smart-home glue like and
  home-assistant): use `toEntities: [world]` with comment explaining
  the by-design broad allow.
- **Reusable kustomize components**: `network-policy/baseline` (5
  additive allows: DNS, apiserver, host-probes, intra-ns,
  monitoring-scrape) + `network-policy/default-deny` (1 CNP, no
  audit-mode annotation). Per-namespace `default-deny` ships as
  separate Flux Kustomization after baseline soaks.

## Known follow-ups outside this rollout

- **Issue #11493**: arr-mcp (legacy SSE vs streamable-http) and
  github-mcp (missing auth config) — not netpol. Recommend opening
  as separate issues.
- **kuadrant ns** is dormant in cluster (`kustomization.yaml`
  commented out). Lockdown applies when kuadrant is re-enabled.
- **Cert renewal end-to-end test** hasn't been triggered post-FQDN
  fix; first ACME renewal in ~60 days will validate.

## Goal

Lock the cluster down with deny-by-default `CiliumNetworkPolicy`
(CNP) so pod-to-pod and pod-to-egress traffic is permitted only by
explicit allow rules. Keep the cluster fully operational during the
rollout: no app outage, no Flux reconciliation stall, no apiserver
isolation, no DNS isolation.

## Current state (snapshot)

- Cilium 1.19, `enable-policy=default`, `policy-cidr-match-mode=""`
  (this is the precondition for the apiserver footgun — see Risks).
- Hubble enabled cluster-wide, `hubble-network-policy-correlation-enabled=true`,
  metrics on `:9965` already scraped. We have flow visibility today.
- Existing policies are sparse and intentional:
  - `vpn/downloads-gateway-pod-gateway` + reflected copy in `default`
    (pod-gateway VXLAN/WireGuard allow).
  - `flux-system/allow-webhooks-from-gateway` + `flux-operator-web`.
  - A handful of chart-shipped K8s `NetworkPolicy` resources
    (zulip-memcached/rabbitmq, pgadmin, grafana-image-renderer).
- Flux's bundled NetworkPolicies are **disabled** in
  `flux-instance` values (`cluster.networkPolicy: false`) — Flux
  controller egress is currently unrestricted, which simplifies
  rollout (we don't risk source-controller losing GitHub access on
  day one).
- DNS = CoreDNS in `kube-system`, selector
  `{k8s-app: kube-dns, app.kubernetes.io/name: coredns}`.
- Ingress = Envoy Gateway pods in `network` ns
  (`external` LB 10.45.0.13, `internal` 10.45.0.12).
- Nodes are spread across `192.168.1.x` (trusted LAN) and
  `192.168.4.x` (VLAN 20, also trusted at the cluster layer).
- Gateway = brain at `192.168.6.1` (router) and `192.168.6.66`
  (BIND/pihole) — both reachable as `reserved:host` from pods'
  perspective via Cilium identity.

This means **today** we have flow visibility, a working pod-gateway
allow, and zero default-deny. Good starting position.

---

## Guiding principles

1. **Standardize on `CiliumNetworkPolicy`, not vanilla K8s
   `NetworkPolicy`.** Cilium's identity-based selectors
   (`reserved:kube-apiserver`, `reserved:host`, `fromEntities`,
   `toFQDNs`, `toServices`) are the only way to safely allow some
   flows in this cluster. Mixing the two forms makes precedence and
   debugging painful.
2. **No `toCIDR: 0.0.0.0/0` for cluster-internal targets.** Per
   `project_cilium_ipblock_apiserver.md`, `0.0.0.0/0` does **not**
   match `kube-apiserver` (host-network identity). Use entity
   selectors. The pod-gateway egress is the documented exception
   (WireGuard UDP/51820 to true world).
3. **Allow before deny.** Every namespace gets the baseline
   allow-policies merged and Hubble-verified *before* the
   default-deny policy lands in the same PR. Default-deny is the
   *last* manifest applied in a namespace, never the first.
4. **One namespace per PR.** Blast radius capped at one namespace,
   one Flux Kustomization, one revert.
5. **System namespaces last.** `kube-system`, `flux-system`,
   `cilium-secrets`, `rook-ceph`, `longhorn-system`,
   `external-secrets`, `cert-manager`, `network` — these get
   default-deny *after* every app namespace is locked down and
   stable. Breaking one of these breaks the cluster.
6. **Suspended Kustomizations are not in scope.** If a Flux
   Kustomization is `spec.suspend: true`, skip its namespace until
   it's unsuspended. Don't touch suspended state.

---

## Baseline policies (per-namespace)

These four CNPs ship together as a reusable component at
`kubernetes/components/network-policy/baseline/` and get added to
each namespace's `kustomization.yaml` as the namespace enters the
rollout. The default-deny is in a **separate** Kustomization
applied *after* the allow-policies have soaked for at least 24h.

### 1. allow-dns.yaml

```yaml
---
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: allow-dns
spec:
  endpointSelector: {}            # all pods in namespace
  egress:
    - toEndpoints:
        - matchLabels:
            io.kubernetes.pod.namespace: kube-system
            k8s-app: kube-dns
      toPorts:
        - ports:
            - port: "53"
              protocol: UDP
            - port: "53"
              protocol: TCP
          rules:
            dns:
              - matchPattern: "*"
```

The L7 DNS rule is critical — it enables Cilium's DNS-FQDN cache
so we can later write `toFQDNs:` rules for external egress
(GitHub, Cloudflare, etc.).

### 2. allow-apiserver.yaml

```yaml
---
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: allow-apiserver
spec:
  endpointSelector: {}
  egress:
    - toEntities:
        - kube-apiserver
      toPorts:
        - ports:
            - port: "6443"
              protocol: TCP
```

**Do not** use `toCIDR` for this. The `reserved:kube-apiserver`
entity is the only form that works given our current
`policy-cidr-match-mode=""`.

### 3. allow-intra-namespace.yaml

```yaml
---
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: allow-intra-namespace
spec:
  endpointSelector: {}
  ingress:
    - fromEndpoints:
        - matchLabels: {}
  egress:
    - toEndpoints:
        - matchLabels: {}
```

Permits the common "deployment X talks to its sidecar/companion
deployment Y in the same namespace" pattern without per-app rules.

### 4. allow-monitoring-scrape.yaml

```yaml
---
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: allow-monitoring-scrape
spec:
  endpointSelector: {}
  ingress:
    - fromEndpoints:
        - matchLabels:
            io.kubernetes.pod.namespace: observability
```

Prometheus and Hubble metrics scraping work without enumerating
every `/metrics` port.

### 5. default-deny.yaml (applied LAST, separate Kustomization)

```yaml
---
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: default-deny
spec:
  endpointSelector: {}
  ingressDeny:
    - {}
  egressDeny:
    - {}
```

Use Cilium's `ingressDeny`/`egressDeny` (1.14+) instead of the
empty-allow pattern — it's explicit and composable. With
`enable-non-default-deny-policies=true` already set (confirmed in
cilium-config), this is the right primitive.

---

## App-specific allow patterns

Each pattern below is a template overlay that sits *next to* the
baseline in the app's namespace. The endpointSelector should
target the specific app's pod label (typically
`app.kubernetes.io/name`).

### Pattern A: ingress-only web app (Authelia, Glance, public web UIs)

```yaml
---
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: allow-ingress-from-gateway
spec:
  endpointSelector:
    matchLabels:
      app.kubernetes.io/name: <app>
  ingress:
    - fromEndpoints:
        - matchLabels:
            io.kubernetes.pod.namespace: network
            app.kubernetes.io/name: envoy
```

### Pattern B: web app + CNPG database (same cluster, cross-namespace)

Add to Pattern A:

```yaml
  egress:
    - toEndpoints:
        - matchLabels:
            io.kubernetes.pod.namespace: databases
            cnpg.io/cluster: <pg-cluster-name>
      toPorts:
        - ports: [{port: "5432", protocol: TCP}]
```

CNPG pods carry the `cnpg.io/cluster: <name>` label — leverage it
instead of `app.kubernetes.io/name`.

### Pattern C: CNPG cluster (the database itself) + Garage backup target

Allow apiserver (baseline), allow ingress from any same-cluster
consumer namespace, allow egress to Garage's LB IP via FQDN:

```yaml
---
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: allow-garage-s3
spec:
  endpointSelector:
    matchLabels:
      cnpg.io/cluster: <name>
  egress:
    - toFQDNs:
        - matchName: "s3.${SECRET_DOMAIN}"
      toPorts:
        - ports: [{port: "443", protocol: TCP}]
```

`toFQDNs` resolves via the L7 DNS rule in the baseline. Garage's
LB IP (10.45.0.x range, advertised via BGP) is reachable; Cilium
matches the resolved IP against the FQDN cache.

### Pattern D: media-pull-stack internal deps

Same as Pattern B but with `app.kubernetes.io/name` selectors for
peer apps in the same `media` namespace — Pattern 3 (intra-ns
allow) in the baseline already covers this. The extra rule needed
is **egress to indexers** via toFQDNs (or, where indexers rotate
IPs aggressively, a wider `toEntities: [world]` rule scoped to the
single pod):

```yaml
  egress:
    - toFQDNs:
        - matchPattern: "*.indexer.example.com"
      toPorts:
        - ports: [{port: "443", protocol: TCP}]
```

### Pattern E: VPN-routed app (pod-gateway client)

These already work via the existing `downloads-gateway-pod-gateway`
CNP in the routed namespaces. **Do not** add a default-deny to
those namespaces (`downloads`, parts of `default`) until the
pod-gateway interaction is re-verified with Hubble — the VXLAN
encapsulation makes flow inspection trickier. Plan: pod-gateway
namespaces get default-deny in a dedicated Phase 5b sub-step.

### Pattern F: Camera ingress (Frigate → Reolink on iot VLAN 192.168.4/5)

```yaml
---
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: allow-cameras
spec:
  endpointSelector:
    matchLabels:
      app.kubernetes.io/name: frigate
  egress:
    - toCIDR:
        - 192.168.5.0/24    # camera VLAN, adjust to actual prefix
      toPorts:
        - ports:
            - {port: "554", protocol: TCP}    # RTSP
            - {port: "80",  protocol: TCP}    # ONVIF
            - {port: "443", protocol: TCP}
```

CIDR is fine here — cameras are not on host network, so
`policy-cidr-match-mode` quirk doesn't apply.

### Pattern G: Egress to LB IP on same cluster (Mealie → Garage, etc.)

Prefer `toFQDNs` (Pattern C). If FQDN is unavailable, use
`toEntities: [cluster]` scoped to the specific port — this matches
any in-cluster identity including LB-targeted pods.

### Pattern H: external-dns, external-secrets, cert-manager egress

Single dedicated rule per controller, allow specific FQDNs:
`api.cloudflare.com`, the 1Password Connect operator's in-cluster
Service, `acme-v02.api.letsencrypt.org`. These ship as part of the
system-namespace lockdown in Phase 6.

---

## Cilium gotchas (read before writing any policy)

1. **`reserved:kube-apiserver`** — only safe form for apiserver
   egress. See `project_cilium_ipblock_apiserver.md`. Re-check
   before merging any CNP that touches port 6443.
2. **`reserved:host` vs `reserved:remote-node`** — `host` matches
   only the local node's IP from the pod's perspective; use it for
   things like node-local readiness probes. `remote-node` covers
   *other* cluster nodes. Both differ from `kube-apiserver` (which
   is identity-based on the apiserver pod, even when host-networked).
3. **`fromEntities: [cluster]` is broad.** It matches any
   in-cluster identity. Use only when you can't tighten further.
4. **DNS L7 rule required for `toFQDNs`.** No L7 DNS rule → FQDN
   cache empty → FQDN egress silently drops. The baseline DNS
   policy includes `rules.dns.matchPattern: "*"` precisely for
   this.
5. **`enableDefaultDeny: false` escape valve.** Setting this on a
   CNP makes it additive-only (won't trigger default-deny behavior
   even if it's the only policy on those endpoints). Use this for
   the *baseline allow* policies so they don't accidentally
   default-deny a namespace before the explicit default-deny
   policy lands.
6. **Hubble flow correlation.** With
   `hubble-network-policy-correlation-enabled=true` (already on),
   Hubble flows include the matching policy name in
   `policy_match_type` and `egress_allowed_by`. Use this:

   ```bash
   hubble observe --namespace <ns> --verdict DROPPED --last 100
   hubble observe --namespace <ns> --verdict DROPPED -f
   ```

7. **`policy-enforcement: audit` mode.** Cilium supports a
   per-endpoint audit mode via the `policy.cilium.io/audit-mode`
   annotation. We will **use audit mode for one rollout cycle per
   namespace** (apply baseline + default-deny with audit annotation,
   tail Hubble for `DROPPED-AUDITED` verdicts for 48h, then flip
   the annotation off). Cluster-wide
   `policy-enforcement-mode: audit` is **not** proposed — too
   coarse, and we'd have no signal of what's actually enforced
   anywhere.

---

## Cross-cluster / external dependencies

These flows must remain working throughout the rollout. Each row
notes whether it falls inside NetworkPolicy scope.

| Flow | Source | Destination | In CNP scope? | Handling |
|---|---|---|---|---|
| BGP node ↔ brain | cilium-agent (host net) | 192.168.6.1:179 | No — host network | n/a |
| Pod DNS → CoreDNS | pods | kube-system kube-dns | Yes | baseline `allow-dns` |
| Pod DNS → brain BIND | pods (rare, direct) | 192.168.6.66:53 | Yes (if any app does this) | per-app `toCIDR: 192.168.6.66/32` |
| Apiserver | pods using k8s clients | reserved:kube-apiserver | Yes | baseline `allow-apiserver` |
| Garage S3 | CNPG, immich, paperless, rclone | s3.${SECRET_DOMAIN} LB IP | Yes | Pattern C `toFQDNs` |
| external-dns → Cloudflare | external-dns pod | api.cloudflare.com:443 | Yes | Phase 6 per-namespace allow |
| external-secrets → 1P Connect | external-secrets pod | 1p Connect Service (in cluster) | Yes | Phase 6 intra-cluster allow |
| Flux → GitHub | source-controller | github.com:443 (via DNS) | Yes | Phase 6 `toFQDNs: github.com, codeload.github.com, *.githubusercontent.com` |
| cert-manager → Let's Encrypt | cert-manager | acme-v02.api.letsencrypt.org | Yes | Phase 6 `toFQDNs` |
| Envoy Gateway → backend pods | network ns envoy pods | app pods | Yes | every app namespace adds Pattern A |
| Hubble metrics scrape | observability prom | port 9965 on every node | Mixed | baseline `allow-monitoring-scrape` covers pod-level; host-net scrape is out of scope |
| pod-gateway WireGuard | downloads-gateway pod | 0.0.0.0/0:51820 UDP | Yes | existing CNP preserved |

---

## Phasing

### Phase 0 — Audit & instrumentation (1 week)

- Stand up a Grafana dashboard sourced from Hubble metrics
  filtering on `verdict=DROPPED` and `verdict=DROPPED-AUDITED`,
  bucketed by source/destination namespace. This is the rollout's
  primary feedback loop.
- Write a Hubble query cheat-sheet into `docs/src/` for the
  per-phase verification step.
- Add `kubernetes/components/network-policy/baseline/` with the
  five baseline policies (allow-dns, allow-apiserver,
  allow-intra-namespace, allow-monitoring-scrape, default-deny),
  the first four with `enableDefaultDeny: false`, the fifth as a
  separate file that the per-namespace Kustomization opts into
  explicitly.
- **No policy applied to any namespace in this phase.**

Exit criteria: Hubble drop dashboard live and trusted; baseline
component built and `kustomize build` clean.

### Phase 1 — Canary namespace: `selfhosted` (1 week)

Why `selfhosted`: lowest-risk namespace in the repo — currently
contains exactly one app (`webhook`, 2 replicas) with no live
traffic per the Phase 0 Hubble survey. Not a system namespace.

**Scope note (2026-05-17):** This canary proves the *mechanic* —
the baseline component applies cleanly, CNPs land in the right
namespace, audit-mode soak surfaces no surprises. It does **not**
exercise the Pattern A/B/C overlay surface meaningfully because
`webhook` is idle. Real pattern coverage starts in Phase 2 across
7 namespaces; don't gate the Phase 2 start on Phase 1 surfacing
allow-pattern needs that won't appear here.

Steps:

1. PR 1: merge baseline allow-policies into `selfhosted` ns
   (allow-dns, allow-apiserver, allow-intra-namespace,
   allow-monitoring-scrape). Watch Hubble drops for 24h — there
   should be **none** caused by these policies (they're additive
   with `enableDefaultDeny: false`).
2. PR 2: per-app overlays — Pattern A for each ingressed app,
   Pattern B for any app with a CNPG dep, Pattern C/G for any app
   touching Garage.
3. PR 3: apply `default-deny` with
   `policy.cilium.io/audit-mode: enabled` annotation on a single
   pod first (the lowest-risk app, e.g. `glance`). Tail Hubble for
   48h for `DROPPED-AUDITED` verdicts. Add allow rules for any
   legitimate flow surfaced.
4. PR 4: remove audit annotation, expand `default-deny` to the
   whole namespace.

Exit criteria: full namespace under default-deny, zero unexplained
drops over 24h, all apps respond to smoke tests.

### Phase 2 — Stateless app namespaces (2-3 weeks)

Order by criticality, low-to-high: `ai`, `actions-runner-system`,
`renovate`, `mcp-system`, `home`, `collab`, `media`.

Same 4-PR pattern per namespace. Audit mode for each first app per
namespace; once the pattern catalog stabilizes, audit mode becomes
optional for low-risk additions.

### Phase 3 — Stateful & ingress-critical namespaces (1-2 weeks)

`auth` (Authelia — breaking this locks the user out of every
OIDC app), `databases` (CNPG control plane), `vpn` (pod-gateway
server), `downloads` (pod-gateway clients).

For `auth`: explicit pre-flight check that LLDAP, Authelia, and
every OIDC client's redirect URL still resolves and authenticates
*before* default-deny lands.

For `databases`: every CNPG cluster needs an explicit allow rule
from its consumer namespace(s). Audit mode mandatory; tail for
72h.

For `vpn` + `downloads` (pod-gateway server + clients) — these
two namespaces must be locked down together as one sub-step.
`vpn` runs `downloads-gateway-pod-gateway-main-0` (the actual
WireGuard egress pod, host-net + VXLAN-terminating); `downloads`
runs the client pods that route through it. The existing
`downloads-gateway-pod-gateway` CNP on the gateway side must be
preserved; the client side gets a *new* baseline-with-pod-gateway-allow.
VXLAN encapsulation makes Hubble flow inspection trickier — audit
for 1 full week before flipping enforce. Added 2026-05-17 after
the original plan missed `vpn` as a distinct namespace.

### Phase 4 — Storage namespaces (1 week)

`storage` (Garage), `longhorn-system`, `rook-ceph`. These mostly
need:

- Intra-namespace allow (baseline).
- Apiserver allow (baseline).
- Specific allow from any consumer namespace (CNPG → Garage S3,
  apps → Longhorn CSI, etc.).
- Node-port reachability — verify Longhorn engine ↔ replica TCP
  reachability via Hubble before default-deny lands.

### Phase 5 — Gateway / cert / secrets namespaces (1 week)

`network` (Envoy Gateway), `cert-manager`, `external-secrets`.

- `network` ingress is from `world` (LoadBalancer) — must allow
  `fromEntities: [world]` on Envoy listeners explicitly.
- `cert-manager` needs egress to `acme-v02.api.letsencrypt.org`
  and to in-cluster Envoy for HTTP-01 challenges.
- `external-secrets` needs egress to the 1P Connect Service.

### Phase 6 — Remaining system namespaces (last, with extra care)

`observability`, `kuadrant`, `istio-system`, `cilium-secrets`.

`flux-system` and `kube-system` are explicitly out of scope (see
"Decisions" and "Out of scope"). Skip them entirely.

For `observability`: Prometheus needs egress to every namespace
on metrics ports — this is the *opposite* direction from baseline
`allow-monitoring-scrape`. Add an explicit
`toEndpoints: matchLabels: {}` rule with the metrics ports
enumerated.

For `cilium-secrets`: minimal workload (mostly Secret consumers).
Baseline + intra-namespace should suffice; audit 1 week. **Mechanic
note (2026-05-17):** this namespace is auto-managed by the cilium
chart — there is no `kubernetes/apps/cilium-secrets/` directory.
Adding policies requires either (a) cilium chart values that drop
CNPs into the namespace, or (b) a new dedicated Flux Kustomization
under `kubernetes/apps/kube-system/cilium/` that targets
`cilium-secrets`. Decide the mechanism when we get there;
not the same per-namespace PR pattern as the rest.

---

## Rollout mechanics

- **Branch + PR per namespace.** Branch name `netpol/<ns>`. Each
  PR adds the baseline component to the namespace's
  `kustomization.yaml`, plus per-app overlays.
- **Default-deny is its own PR**, after the allow PR has soaked
  ≥24h with zero unexplained Hubble drops.
- **Pre-merge checklist (in PR template):**
  - [ ] Hubble flow-tail for the target namespace shows allowed
        traffic matches an expected baseline or per-app rule.
  - [ ] `kubectl get cnp -n <ns>` shows only expected policies.
  - [ ] App smoke test (curl ingress URL, check ready endpoints).
  - [ ] `flux get all -n <ns>` clean.
- **Vanilla `NetworkPolicy` in target namespace:** if a chart ships
  a vanilla `kind: NetworkPolicy` (the 7 known cases: zulip-memcached,
  zulip-rabbitmq, pgadmin-pgadmin4, grafana-image-renderer,
  the two flux-system policies, vpn pod-gateway), convert it to
  `CiliumNetworkPolicy` in the same per-namespace PR. Decision
  locked 2026-05-17: convert per-namespace as we reach them rather
  than batching upfront.
- **Rollback (fast):**
  1. `flux suspend kustomization -n flux-system <ns>-app`.
  2. `kubectl delete cnp -n <ns> default-deny`.
  3. Confirm traffic restored via Hubble.
  4. Revert the PR, `flux resume`.
- **Rollback (full namespace):**
  1. `flux suspend kustomization -n flux-system <ns>-app`.
  2. `kubectl delete cnp -n <ns> --all` (excluding pre-existing
     allow-policies you want to keep — check before nuking).
  3. Revert the PR.

---

## Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 1 | Apiserver isolation via wrong egress rule (ipBlock footgun) | Medium | Critical (controller manager, kubelet, every operator wedges) | Use only `reserved:kube-apiserver` entity; baseline policy reviewed against `project_cilium_ipblock_apiserver.md`; audit mode for every first-pass policy |
| 2 | DNS isolation (CoreDNS unreachable from a pod) | Medium | Critical (every name resolution fails, cascading app failures) | Baseline `allow-dns` ships with every namespace before default-deny; Hubble verify CoreDNS traffic flowing post-apply |
| 3 | Ingress break for user-facing apps (Authelia, Immich, public-app stack) | Medium | High (user locked out of OIDC apps, external services down) | Phase 3 dedicated step for `auth`, audit mode mandatory, pre-flight smoke test of Authelia + 2 dependent OIDC clients |
| 4 | CNPG → Garage backup target broken | Medium | High (no CNPG backups, but apps still serve) | Pattern C uses `toFQDNs`; verify `barman-cloud` log post-apply shows successful upload; Phase 4 audit window 72h |
| 5 | VPN-gateway double-policy conflict | Medium | Medium (downloads stack offline, but easily reverted) | Phase 3 sub-step, audit for 1 week; preserve existing `downloads-gateway-pod-gateway` CNP unchanged |
| 6 | Flux controllers lose GitHub/apiserver egress | Low (Phase 6) | Critical (no GitOps reconciliation, including the very revert that fixes it) | Phase 6 last; never enable Flux chart NPs; ship own CNP with `toFQDNs: github.com`; pre-validate with audit mode 2 weeks |
| 7 | External-secrets loses 1P Connect → secret rotation/refresh stalls | Low | Medium (existing secrets keep working; new/rotated ones fail to materialize) | Phase 5; explicit allow to 1P Connect Service in same cluster |
| 8 | Hubble drop volume overwhelms metrics pipeline | Low | Low (dashboard signal degraded, not cluster impact) | Tune `hubble-metrics` sampling if needed; the `enable-non-default-deny-policies=true` setting reduces double-counting |
| 9 | `toFQDNs` cache miss on first request → intermittent failures | Medium | Low (one retry resolves) | Baseline DNS L7 rule with `matchPattern: "*"`; long DNS TTL respected; document in runbook |
| 10 | Chart-shipped NetworkPolicy (vanilla K8s kind) conflicts with our CNP | Low | Medium (silently more-restrictive intersection) | Audit chart-shipped NPs in scope namespaces during Phase 0; either disable via chart value or document the merge intent in the per-app overlay |

---

## Estimated timeline

| Week | Phase | Deliverable |
|---|---|---|
| 1 | Phase 0 | Hubble dashboard, baseline component, runbook |
| 2 | Phase 1 | `selfhosted` namespace fully default-deny |
| 3-5 | Phase 2 | `ai`, `actions-runner-system`, `renovate`, `mcp-system`, `home`, `collab`, `media` |
| 6-7 | Phase 3 | `auth`, `databases`, `vpn`, `downloads` |
| 8 | Phase 4 | `storage`, `longhorn-system`, `rook-ceph` |
| 9 | Phase 5 | `network`, `cert-manager`, `external-secrets` |
| 10-11 | Phase 6 | `observability`, `kuadrant`, `istio-system`, `cilium-secrets` |

User sets pace. Any phase can park indefinitely without affecting
prior phases — namespaces locked down stay locked down.

---

## Out of scope

- **`flux-system` namespace.** Decision locked 2026-05-17. Flux
  egress to GitHub + apiserver is the rollback mechanism for this
  whole rollout; the perimeter (1P/SSO/CF-tunnel) handles the
  meaningful threat boundary. Revisit only if perimeter assumptions
  change.
- **`kube-system` namespace.** Decision locked 2026-05-17. CoreDNS,
  apiserver, etcd, kubelet — host-network or control-plane
  components where CNP adds risk disproportionate to benefit.
- Host firewalld rules on nodes (brain, masters, workers).
- BGP filtering (Cilium peers freely with brain today).
- Mesh-layer policy (Istio AuthorizationPolicy) — not part of this
  CNP rollout.
- Audit/SIEM integration for drop events — Hubble metrics +
  Grafana is the cluster's tool for this rollout.
