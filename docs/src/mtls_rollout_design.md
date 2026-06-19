# Istio mTLS Rollout Design Proposal

Status: **Proposal — research only, not implemented.** Tier 4 of the
network security roadmap (after NetworkPolicy lockdown and egress
restriction).
Owner: home-ops
Last updated: 2026-05-18

## Problem statement

Istio is installed today but scoped to a single namespace (`mcp-system`).
Eleven MCP server pods carry `sidecar.istio.io/inject: "true"` and
participate in the mesh; the istiod control plane's allow-list
([`cnp-allow.yaml`](https://github.com/rwlove/home-ops/blob/main/kubernetes/apps/istio-system/istio/app/istiod/cnp-allow.yaml))
deliberately opens XDS to `fromEntities: cluster` because "new sidecars
can appear in any namespace" — i.e. the door is held open for expansion,
but no other namespace has walked through it.

The cluster's current network posture (see
[NetworkPolicy Rollout Plan](networkpolicy_rollout_plan.md) and
[Egress Restriction Design](egress_restriction_design.md)) gives us
**L3/L4** controls: which pod can talk to which pod on which port,
based on Cilium identities. What it does **not** give us:

- **Cryptographic peer identity.** A CNP allow rule that says
  "`pump-cv` can reach `immich`" trusts that the source pod really is
  `pump-cv` because Cilium says so based on labels. If an attacker
  takes over a different pod in `collab` and shares enough label
  surface, the identity model can be coaxed.
- **In-flight confidentiality.** Pod-to-pod traffic on the cluster
  overlay is plaintext. A compromised node, a misconfigured tap, or
  a future Cilium CVE that leaks pod-to-pod frames exposes every
  database session and HTTP header in the cluster.
- **Per-workload authentication at L7.** Istio
  `AuthorizationPolicy` can require an mTLS-verified SPIFFE identity
  before accepting a request, which is a strictly tighter check than
  "the packet arrived from a pod in namespace X."

mTLS via Istio would close those gaps. The question this doc evaluates
is whether the cost of running a service mesh across more than the
existing single namespace is justified at this cluster's scale.

## Goal

Evaluate, but do not commit to, extending Istio mTLS beyond `mcp-system`.
Produce:

1. A concrete threat model that distinguishes what mTLS catches that
   default-deny CNPs do not.
2. A phased rollout that the operator could execute incrementally and
   roll back at any point.
3. A clear-eyed recommendation on whether to ship, defer, or never.

Constraints from the cluster's reality:

- **Single operator.** Every new control plane component is a thing
  one person must understand, monitor, and triage at 02:00.
- **Approximately 422 pods.** Of those, ~75% would be candidates for
  sidecar injection (the rest are hostNetwork, daemonsets, batch
  jobs, or operators).
- **Cilium 1.19 is CNI today.** No plan to replace it. Istio sidecar
  and Cilium are well-tested together; ambient mode requires more
  care (see § Architectural choice).
- **NetworkPolicy rollout is freshly rolled back** (2026-05-18) after
  a verification-gap incident. The cluster has lost trust in
  "lock-down then verify" workflows; any mTLS rollout has to do
  better.

## 1. Threat model — what mTLS catches that default-deny does not

The NetworkPolicy work answers the question "which pods can talk to
which other pods." mTLS answers the question "is the pod on the other
end of this connection actually the workload it claims to be, and is
the channel between us tamper-proof."

Three concrete scenarios where the answers diverge:

### Scenario A: lateral movement via shared-namespace label collision

A vulnerability in `collab/searxng` (user-driven web scraper, broad
egress allow per egress design's "inherently broad" bucket) gets RCE.
The attacker controls a pod in `collab`.

- **What CNPs catch.** Any cross-namespace flow that wasn't explicitly
  allowed. The attacker cannot reach `databases` directly because no
  CNP allows `collab/searxng → databases/*`.
- **What CNPs miss.** The `allow-intra-namespace` baseline CNP lets
  any pod in `collab` talk to any other pod in `collab`. The attacker
  can reach `pump`, `pump-cv`, `obsidian-couchdb`, `paperless`, etc.
  on whatever ports their respective allow-CNPs expose.
- **What mTLS adds.** With `STRICT` PeerAuthentication and an
  AuthorizationPolicy on (e.g.) `pump` that requires
  `principals: ["cluster.local/ns/collab/sa/pump"]`, a request from
  `collab/searxng` is rejected at the receiving sidecar even though
  the L4 connection succeeded. The attacker needs to also steal a
  ServiceAccount token *and* prove possession of its X.509 cert,
  which the sidecar refuses to issue without a valid SA.

This is the highest-value scenario. The cluster's threat surface is
shaped like "many apps in one namespace per concern" — `collab` has
17 apps, `media` has 30+. Intra-namespace movement is the unfilled
gap.

### Scenario B: compromised node taps pod-to-pod overlay traffic

A worker node is compromised — disk image stolen, kubelet credentials
extracted, or the host gets RCE via a kernel CVE. The attacker can
run `tcpdump` on the node's interfaces and see every cni0/vxlan
frame transiting it.

- **What CNPs catch.** Nothing — CNPs gate which connections form,
  not what's on the wire after they do.
- **What mTLS adds.** Every mesh-internal connection is wrapped in
  TLS 1.3 with workload-cert client + server auth. Captured frames
  are encrypted; the attacker would also need the receiving pod's
  private key (only present inside the receiving sidecar's memory).

This scenario is real but lower-probability in a home lab. The
mitigation that already exists is *node access control* — physical
access, no shared tenants, hardened kubelet. mTLS adds defense in
depth here, not a new primary control.

### Scenario C: a future Cilium identity-confusion bug

The 2026-05-17/18 rollback discovered that Cilium's identity model
can be wrong in subtle ways: socket-lb rewrites destinations before
policy evaluation; `matchPattern` silently fails on canonical FQDNs;
VXLAN-encapsulated broadcast frames are dropped because identity
doesn't propagate. None of those were attacker-exploitable, but they
were *correctness* bugs in the very mechanism mTLS would protect
against. If a future Cilium CVE lets an attacker spoof a pod's
identity to a peer pod (presented to the receiving CNP as a different
endpoint), CNPs no longer protect anything.

- **What mTLS adds.** Workload identity is bound to a Kubernetes
  ServiceAccount via an X.509 cert issued by istiod. It is **not**
  derived from Cilium identity. An attacker who can confuse Cilium's
  identity table still cannot present a `pump.collab.svc.cluster.local`
  client cert without compromising the istiod CA or stealing the
  pump SA token.

This is the "defense in depth against tools we already rely on"
argument. The strength of this argument is proportional to how much
you trust Cilium's identity model — and our recent rollout chewed
through some of that trust.

### What mTLS does NOT add

To be honest about what we get:

- **No protection against compromised mTLS-participating pods.** If
  `pump-cv` is itself compromised, it has a valid cert and the mesh
  trusts it.
- **No protection at the ingress edge.** External traffic
  (cloudflared → envoy gateway → backend) is already TLS-terminated
  at envoy. mTLS inside the cluster doesn't change the public
  threat model.
- **No mitigation for the data-exfil class.** Egress to the internet
  is bound by Cilium FQDN/entity rules, not mesh policy.
- **No protection against control-plane (istiod) compromise.**
  istiod is the CA. It's a new SPOF.

## 2. Inventory — services with native mTLS today vs. what Istio would add

### What already has TLS/mTLS in flight

| Component | Channel | Mechanism |
|---|---|---|
| Cloudflared → Envoy Gateway | external ingress | Envoy TLS termination with per-route certs from cert-manager |
| Envoy Gateway → some backends | internal HTTPS | Re-encrypted backends (jellyfin, immich on TLS); most are plaintext |
| CNPG client → CNPG primary | DB replication + barman | CNPG generates server certs; client `verify-ca` mode |
| Cilium agent → Hubble | telemetry | Cilium-managed certs |
| `mcp-system` sidecars ↔ istiod | XDS | Already mTLS (port 15012) — this is the deployed slice |

### What Istio sidecar would add

For any pod with `sidecar.istio.io/inject: "true"`:

- Outbound: all traffic transparently routed through the sidecar,
  upgraded to mTLS when the destination is also in the mesh.
- Inbound: all traffic terminated by the sidecar, mTLS verified.
- Per-port plaintext fallback (`PERMISSIVE` PeerAuthentication) until
  every peer is in the mesh.

### Pods that cannot run a sidecar (sidecar mode)

| Pod type | Examples | Why |
|---|---|---|
| `hostNetwork: true` | `kube-vip` (daemonset), `etcd-defrag` (cronjob) | Sidecar's iptables redirect doesn't work in host net namespace |
| Daemonsets serving node-level data | `cilium-agent`, `node-exporter`, `node-problem-detector`, `node-tuning` | Same reason; many also need raw socket / capability access incompatible with sidecar UID |
| Multi-interface pods | `vpn/pod-gateway`, `downloads/*` clients (VXLAN to gateway) | Sidecar redirect interferes with VXLAN tunnel; verified-incompatible by the netpol rollout's pod-gateway carve-out |
| Bare TCP non-HTTP that's deeply protocol-coupled | EMQX MQTT brokers, Zigbee2MQTT, Z-Wave-JS, EsphomeAPI | Istio supports TCP-only services, but loses L7 features (AuthorizationPolicy reduces to L4 source identity only) |
| Pods that read `/proc` or use host PID/network | `node-tuning`, `gpu-operator` driver daemons | Same as daemonset bucket |
| Operators that talk to apiserver only | `cnpg-controller`, `external-secrets`, `cert-manager`, `flux-system/*` | Adds latency + complexity for ~zero security benefit; apiserver traffic is already mTLS via SA tokens |
| Init-container-heavy short-lived workloads | `cnpg-bootstrap`, longhorn engine restore jobs | Sidecar startup timing makes init containers flaky; documented Istio quirk |

### Pods that should NOT join the mesh (judgment, not technical)

| Bucket | Reason |
|---|---|
| `flux-system` controllers | Rollback substrate. Adding sidecars to flux-controller is "modify the tool you use to fix mistakes." |
| `cilium-*` (in `kube-system` + `cilium-secrets`) | Cilium is the data plane Istio runs on top of. Circular dependency on bootstrap. |
| `rook-ceph` operator + agents | Sidecars on storage data path add latency and a new failure mode for the very PVCs the mesh apps depend on. |

### What's left — the mesh-eligible surface

Approximately 75% of cluster pods, concentrated in: `collab`, `media`,
`ai`, `home`, `mcp-system` (already done), `auth`, `databases`
(CNPG primaries can be meshed; replication channels stay native),
`observability` (grafana/loki/prometheus can be meshed; alloy/vector
daemonsets cannot).

## 3. Architectural choice — sidecar mode vs. ambient mode

Istio 1.22+ ships **ambient mode** as an alternative to per-pod
sidecars. The choice is load-bearing for everything that follows.

### Sidecar mode

**How it works.** Every meshed pod gets an injected `istio-proxy`
container (Envoy). All inbound + outbound pod traffic is redirected
to the sidecar via init-container iptables rules. The sidecar
performs mTLS, policy enforcement, L7 telemetry.

**Pros for this cluster:**

- Battle-tested. Sidecar mode is what mcp-system runs today; we have
  six months of operational experience.
- Per-pod blast radius. A failing sidecar takes down one pod, not a
  node.
- Well-supported by `app-template` chart via pod annotations — no
  new chart machinery needed.

**Cons:**

- **Resource overhead.** Each sidecar costs ~50-100m CPU and ~100Mi
  RAM steady-state. At ~315 meshed pods that's ~16-32 vCPU and
  ~31 GiB RAM — non-trivial on a cluster where worker8 already runs
  92% allocated and P40-era workers are P40-era.
- **Pod-startup latency.** Sidecar must be ready before the app
  can serve. Adds 5-15s to cold starts (already a known pain point
  for media-pull-stack apps, immich-ml, and others — see
  [HelmReleases needing disableWait](https://github.com/rwlove/home-ops) memory note).
- **Init container ordering.** Some apps' init containers need
  network before the sidecar is ready. Workarounds exist
  (`holdApplicationUntilProxyStarts`) but add per-app config.
- **Per-app debug.** A connection failure could be Cilium CNP, Istio
  AuthorizationPolicy, Istio PeerAuthentication, sidecar config,
  or app-level TLS. Five layers, four owners (we own all four).

### Ambient mode

**How it works.** No sidecars. A per-node "ztunnel" daemon handles
mTLS for L4 traffic. An optional per-namespace "waypoint" pod handles
L7 features (AuthorizationPolicy by HTTP method/path, rate limiting,
etc.) when needed. Apps are unchanged — joining the mesh means
labeling a namespace.

**Pros for this cluster:**

- **Per-node overhead instead of per-pod.** One ztunnel per node
  (~100m CPU, ~200Mi RAM each × 9 worker nodes ≈ 900m CPU,
  1.8 GiB RAM) instead of one Envoy per pod. ~20x reduction.
- **No app restart to join the mesh.** Label the namespace; existing
  pods are meshed on next packet. Reversible by removing the label.
- **No init-container ordering issues.** Apps see normal traffic;
  ztunnel does the mTLS off-pod.
- **No 5-15s sidecar warmup on pod restart.**
- **Conceptually cleaner for the "all pods L4-mTLS, only some pods
  L7" pattern** — which describes our cluster's mostly-internal HTTP
  with a few bare-TCP services well.

**Cons:**

- **Newer.** Istio 1.22 (May 2024) GA'd ambient; 1.29 is what we run.
  Production track record is shorter than sidecar mode.
- **ztunnel + Cilium overlap.** Both want to be the per-node data
  plane. Cilium's chaining mode is supported but documented as
  "complex." Specifically, Cilium's socket-lb rewrite (which already
  bit us once) interacts with ztunnel's transparent capture. We
  would need to validate the combination on a non-production cluster
  before committing.
- **AuthorizationPolicy L7 requires waypoints,** which are
  per-namespace pods — back to a sidecar-shaped overhead just for the
  L7 enforcement paths.
- **Smaller upstream community + fewer Stack-Overflow-quality answers**
  for debugging at 02:00.

### Recommendation for this cluster

**If we ship, use sidecar mode for the first 2-3 namespaces, evaluate
ambient on the pilot's lessons.** Rationale:

- mcp-system is already sidecar; matching it removes operational
  complexity.
- Sidecar mode's worst case is "one pod restart loops" — recoverable.
  Ambient mode's worst case is "ztunnel + Cilium data-plane conflict
  takes the node off the mesh" — harder to triage with one operator.
- The ambient-vs-sidecar resource math becomes compelling only
  beyond ~50 meshed pods. The pilot won't get there.
- If sidecar mode shows it scales to 3-4 namespaces without operator
  fatigue, that's the answer. If it doesn't, ambient becomes the
  forced-rewrite alternative — and we'll have real numbers to
  evaluate it against by then.

## 4. Phased rollout

The phasing mirrors the NetworkPolicy rollout's "smallest first, real
verification, one operator-owned change at a time" pattern. The
NetworkPolicy rollback (2026-05-18) established that audit-mode soak +
in-cluster smoke tests are *not* sufficient verification gates.
**Every phase below has a user-driven browser verification step**
before the next phase starts.

### Phase 0 — control-plane scoping (1-2 days)

Today istiod is configured to accept sidecars from any namespace
(`cnp-allow.yaml` uses `fromEntities: cluster`). That is the right
shape for the long run, but istiod has no namespace-discovery
restriction today — it watches every namespace's services + endpoints.

- Add `discoverySelectors` to istiod values, scoped to only the
  namespaces actively in the mesh. Reduces istiod memory + control
  loop cost as more namespaces join.
- Verify mcp-system continues working post-change.

Exit criteria: istiod CPU/memory unchanged or lower; mcp-system MCP
servers continue serving traffic.

**No new namespace joins the mesh in this phase.**

### Phase 1 — pilot namespace: `selfhosted` (2 weeks)

Why `selfhosted`:

- Two pods (`webhook` deployment). Bounded blast radius.
- Already used as the canary for the NetworkPolicy rollout — same
  shape of "smallest, lowest-traffic namespace" gives consistency.
- No CNPG dependency, no external integrations that would obscure
  mesh-introduced failures.
- Already has CNPs in place (`cnp-allow-from-gateway.yaml`), so
  CNP-vs-mesh interaction is exercisable.

Steps:

1. Label `selfhosted` namespace with `istio-injection: enabled`.
2. Restart the `webhook` deployment (forces sidecar injection).
3. **User browser test.** Trigger a webhook end-to-end (the same way
   it gets used in production).
4. Apply `PeerAuthentication: PERMISSIVE` namespace-scoped — allows
   both mTLS and plaintext during the transition. The mesh
   participant accepts mTLS from other mesh peers and plaintext from
   non-mesh (envoy, callers in other namespaces).
5. Soak 1 week. Watch istiod logs for cert issuance errors; watch
   webhook for new restart patterns; watch CPU/memory on the worker
   running the pod.

Exit criteria: webhook serves traffic through sidecar; no new
restart patterns; sidecar CPU under 50m, RAM under 150Mi.

### Phase 2 — second pilot: `collab/pump` + `collab/pump-cv` only (3 weeks)

Why this specific cut, not all of `collab`:

- pump + pump-cv are a *real* multi-pod app with internal
  collab-to-collab traffic (pump-cv is the computer-vision worker
  for pump). Exercises the intra-namespace mTLS path.
- pump is OIDC-protected via Authelia — exercises the
  mesh-to-non-mesh boundary at the ingress edge.
- The 2026-05-18 NetworkPolicy rollback's most-impactful failure
  was `collab/pump` OIDC. Re-meshing it provides a "did we learn
  from the netpol incident" feedback signal: if mesh adoption breaks
  pump again, our verification process is still broken, and we should
  stop.

Steps:

1. Label `pump` and `pump-cv` pods individually with
   `sidecar.istio.io/inject: "true"` (do NOT label the namespace
   yet — keeps other collab apps untouched).
2. Restart pump first; user browser-tests OIDC login end-to-end.
3. Restart pump-cv; user browser-tests image upload + CV worker
   processing end-to-end.
4. **One full week of normal use before any further action.**
5. After soak: add `AuthorizationPolicy` on pump-cv requiring
   `principals: ["cluster.local/ns/collab/sa/pump"]`. This is the
   first real workload-identity check.
6. User browser-tests again to confirm AuthorizationPolicy didn't
   break the pump → pump-cv call.

Exit criteria: pump + pump-cv serving normally; AuthorizationPolicy
in place; no operator alerts.

### Phase 3 — spread one namespace at a time (2-4 months elapsed)

Order, low-to-high risk:

1. `ai` — most apps already restart frequently from model swaps; new
   restart pattern is masked.
2. `home` — careful: home-assistant + esphome + zigbee2mqtt have
   multi-interface and TCP/MQTT patterns. Selectively mesh
   home-assistant first; leave brokers (emqx, z2m) out per § 2.
3. `media` — large surface (30+ apps); take *arr stack first as a
   logical sub-bundle.
4. `collab` (full namespace) — once pump pilot is stable, extend to
   sibling apps.

Each namespace gets:

- Sidecar injection per-pod via existing app-template
  `pod.annotations` (no namespace-label cascade).
- PERMISSIVE PeerAuthentication first, soak 1 week, then STRICT
  per-namespace.
- One user browser-test per OIDC app in the namespace before
  declaring the namespace done.

**Rate limit: one namespace per 2 weeks maximum.** No parallel
rollouts; one operator can't triage two simultaneous mesh failures.

### Phase 4 — STRICT mTLS at the mesh-default level (after 80% adoption)

Once every namespace that's going to be meshed *is* meshed and on
namespace-scoped STRICT PeerAuthentication, replace the per-namespace
PeerAuthentications with a single mesh-scoped one in `istio-system`:

```yaml
apiVersion: security.istio.io/v1
kind: PeerAuthentication
metadata:
  name: default
  namespace: istio-system
spec:
  mtls:
    mode: STRICT
```

This makes mTLS the cluster default. Any new namespace gets STRICT
automatically; opt-out requires an explicit PeerAuthentication.

Exit criteria: no mesh-internal plaintext traffic visible in Kiali /
istio metrics.

### Phase N — never-fully-mesh acceptance

The pods enumerated in § 2 ("Pods that cannot run a sidecar") stay
out of the mesh permanently. Their traffic to meshed pods is allowed
via PeerAuthentication `PERMISSIVE` boundary at the receiving side
or by AuthorizationPolicy carve-outs for specific source identities.

This is fine. The threat model in § 1 already acknowledges that
host-network and daemonset pods have a different trust posture.

## 5. Operational cost

### Resource overhead (sidecar mode)

Empirical numbers from the mcp-system deployment (11 sidecars
currently):

| Per-sidecar resource | Steady-state | P99 |
|---|---|---|
| CPU | 30-70m | 150m |
| RAM | 80-120Mi | 200Mi |
| Startup time added | 5-10s | 15s |

Extrapolated to a hypothetical 80% mesh coverage (~315 pods):

| Cluster-wide cost | Lower bound | Upper bound |
|---|---|---|
| CPU | 9.5 vCPU | 22 vCPU |
| RAM | 25 GiB | 38 GiB |

Cluster has ~80 vCPU and ~520 GiB usable across 9 workers. CPU is
the binding constraint — 22 vCPU is 27% of total. Beast is fine; P40
workers (worker8 already at 92% allocation) are not.

### Resource overhead (ambient mode)

Estimated from upstream docs + ambient-mode performance benchmarks:

| Cluster-wide cost | Estimate |
|---|---|
| ztunnel CPU (9 nodes × ~100m) | 0.9 vCPU |
| ztunnel RAM (9 nodes × ~250Mi) | 2.3 GiB |
| Waypoint CPU (estimate, 5 namespaces with L7 policy × ~100m) | 0.5 vCPU |
| Waypoint RAM (5 × 200Mi) | 1.0 GiB |
| **Total** | **~1.4 vCPU, ~3.3 GiB** |

~15x cheaper. This is the strongest argument for ambient if we get
past the Cilium-interaction concern.

### Management complexity

New CRDs in active use:

- `PeerAuthentication` (mTLS mode)
- `AuthorizationPolicy` (workload identity gates)
- `DestinationRule` (per-destination connection settings; mostly
  unused but learning curve to write one when needed)
- `VirtualService` + `Gateway` (overlap with our existing Envoy
  Gateway / HTTPRoute; we would *not* migrate ingress to Istio
  gateways)
- `ServiceEntry` (for non-mesh external destinations from meshed
  pods)

Per-namespace conventions to establish:

- ServiceAccount-per-pod (today many apps share `default`); without
  per-app SAs, AuthorizationPolicy degrades to namespace-level
  granularity.
- Naming convention for `AuthorizationPolicy` resources to avoid
  cross-app confusion.
- Whether each app belongs in `network-policy/baseline/` style
  shared components, or stays per-app.

Observability additions:

- Kiali (mesh topology view) — currently in repo but namespace
  disabled (per netpol plan § Phase 6 — kuadrant ns dormant; same
  story for kiali).
- Istio control-plane dashboards in Grafana (already shipped by
  kube-prometheus-stack).
- Mesh-aware log fields in Loki — modest work.

### Debugging changes

A failed connection currently has 3 possible causes:

1. CNP denies it.
2. App-level error (auth, app bug).
3. Network plumbing (DNS, kube-proxy, etc.).

With a partial mesh, add five more:

1. PeerAuthentication mismatch (PERMISSIVE vs STRICT cross-boundary).
2. AuthorizationPolicy denies it.
3. Sidecar config error (port not in services, hostNetwork conflict).
4. Cert rotation in flight (ms-scale, but real).
5. Sidecar OOMKilled.

The cluster's MTTR for current-state issues is ~15-30 minutes per
incident. Doubling the failure dimensions plausibly doubles MTTR.
This is the operator-cost line item that matters most for a
one-person operation.

### New SPOFs

- **istiod.** If istiod is down, new pods can join the mesh but
  can't get certs; existing sidecars use cached certs until expiry
  (default 24h). Recoverable, but a new "thing that must be running."
- **Sidecar liveness.** A failing sidecar = a failing pod even if
  the app is fine. New restart patterns to triage.
- **Mesh CA root key.** Rotation procedure exists but is operationally
  finicky; documented Istio gotcha.

## 6. Recommendation

**Defer.** Specifically: keep the current `mcp-system`-scoped
deployment as-is; do not extend mTLS to other namespaces in 2026.

### Rationale

1. **The L4/L7 gap mTLS closes is narrower than it looks here.**
   The cluster's biggest unfixed exposure surface is *egress to the
   internet* (50 of 51 CNPs allow `world:443`), not pod-to-pod
   confidentiality. The
   [egress restriction design](egress_restriction_design.md) addresses
   the higher-value gap first.

2. **The verification-gap lesson from the NetworkPolicy rollback
   (2026-05-18) still applies.** That rollout had stronger
   verification than mesh adoption would have (audit mode + in-cluster
   smoke), and still produced 5+ user-impacting failures. Mesh
   adoption's failure modes (PeerAuthentication mismatches,
   AuthorizationPolicy denies) are *harder* to discover from
   in-cluster tests than CNP drops are, because they're application-
   level and require real authenticated calls to surface.

3. **The operator-cost line is the binding constraint.** One person.
   MTTR doubles with the new failure dimensions. The hours that would
   go into mesh expansion are better spent on the egress restriction,
   the OS migration program (worker2 → master2, beast Stream 9 → 10,
   Talos pilot), and the Spark-arrival-conditional LangGraph
   activation — all of which are higher-leverage for the cluster's
   current shape.

4. **Sidecar resource overhead is non-trivial at this scale.** ~15-32
   vCPU and ~25-38 GiB RAM is plausibly the difference between
   "comfortable headroom" and "next workload needs a new node."
   Ambient mode would solve this, but ambient + Cilium socket-lb
   interaction is exactly the kind of "two clever data planes
   fighting" problem the rollback documented us being bad at.

### Revisit triggers

- **A cluster-internal compromise.** If we ever have a confirmed
  lateral-movement incident, the ROI calculation flips immediately
  and Phase 1 starts the next week.
- **Cilium identity-confusion CVE.** A real one would force the
  "don't trust Cilium identity alone" argument from § 1 Scenario C
  from speculative to concrete.
- **Cluster doubles in size.** At ~800 pods + multi-operator (e.g.
  if the home-ops repo gains a collaborator), the per-operator
  debug-burden math changes.
- **Ambient mode + Cilium chaining gets a "production ready" story
  with multiple home-lab references.** Today the references are
  thin; in 12 months that may change.

### What ships instead

- Document this evaluation (this PR).
- Mention in
  [NetworkPolicy Rollout Plan § Out of scope](networkpolicy_rollout_plan.md)
  that mesh-layer policy was evaluated and deferred. Done already
  in that doc.
- Re-run this evaluation in 2027-Q1 or upon a revisit trigger,
  whichever is sooner.

## What this is NOT

- A teardown of `mcp-system`'s Istio. That deployment serves a
  specific need (XDS-mediated MCP server discovery via Kuadrant)
  and is justified by *that* requirement, not by mTLS-for-mTLS-sake.
  It stays.
- A claim that mTLS is bad. mTLS is genuinely better security than
  CNPs alone. The recommendation is about *cost in this cluster*,
  not *security value in general*.
- A permanent "no." It is a "not now, with concrete triggers for
  reconsideration."
