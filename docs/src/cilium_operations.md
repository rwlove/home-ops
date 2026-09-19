# Cilium Operations

Day-2 operator runbook for Cilium networking in this cluster: health
checks, CiliumNetworkPolicy (CNP) triage, BGP peering, and metrics
interpretation. This is the **operate** doc — for *design and
rationale* see the links in each section below; this doc does not
restate them.

Related docs, not duplicated here:

- [NetworkPolicy Rollout Plan](networkpolicy_rollout_plan.md) —
  per-namespace rollout status, the audit-mode → enforce lifecycle,
  the app-specific allow-pattern catalog (A–H), and the full Cilium
  gotchas list.
- [Egress Restriction Design](egress_restriction_design.md) — the
  detection-first hybrid approach to outbound-to-internet posture,
  Hubble metric caveats, and the egress-anomaly alerting design.
- [Istio mTLS Rollout Design](mtls_rollout_design.md) — mesh mTLS
  scope (`mcp-system` only today) and its relationship to the
  NetworkPolicy layer.
- [`kubernetes/components/network-policy/baseline/README.md`](https://github.com/rwlove/home-ops/blob/main/kubernetes/components/network-policy/baseline/README.md)
  and
  [`kubernetes/components/network-policy/default-deny/README.md`](https://github.com/rwlove/home-ops/blob/main/kubernetes/components/network-policy/default-deny/README.md)
  — the reusable kustomize Components every namespace's CNP set is
  built from.
- [Debugging](debugging.md) § Networking — the quick-check command
  block this doc expands on.

## Cluster topology in one paragraph

Cilium runs in native routing mode (`routingMode: native`,
`autoDirectNodeRoutes: true`) with `kubeProxyReplacement: true` and
`policyDenyResponse: icmp` — a policy-denied connection gets an
immediate ICMP rejection instead of a silent black hole / timeout.
LoadBalancer IPs come from a `CiliumLoadBalancerIPPool` and are
announced to the home router (`brain`) over BGP using the
three-resource model (`CiliumBGPClusterConfig` +
`CiliumBGPPeerConfig` + `CiliumBGPAdvertisement`) — **not** the
retired `CiliumBGPPeeringPolicy`; if you see that kind anywhere it's
drift, not active config (see
`.agents/instructions/schema.correction.md`). Hubble is enabled
cluster-wide with Relay + UI + a Prometheus-scraped metrics
endpoint, which is what backs the drop alerts in
[Metrics interpretation](#metrics-interpretation) below.

Manifests: `kubernetes/apps/kube-system/cilium/app/values.yaml`
(HelmRelease values) and `kubernetes/apps/kube-system/cilium/config/`
(BGP + LB pool CRs).

## Health checks

```sh
# Overall agent + operator + Hubble status (run against any node's agent)
kubectl -n kube-system exec -it ds/cilium -- cilium status --brief

# Full status, including per-feature detail (BPF maps, IPAM, encryption)
kubectl -n kube-system exec -it ds/cilium -- cilium status

# Per-node agent health (all should report OK)
kubectl -n kube-system get pods -l k8s-app=cilium -o wide

# Cilium's own connectivity test suite (safe, read-only against existing
# pods; does not need the full cilium-cli connectivity-test workload)
kubectl -n kube-system exec -it ds/cilium -- cilium-health status
```

### Endpoint and identity state

An "endpoint" is Cilium's per-pod (or per-host-networked-process)
dataplane object; an "identity" is the security label set an
endpoint resolves to for policy matching. Both are worth checking
when a pod's traffic doesn't behave as its CNPs suggest it should.

```sh
# Endpoints on a given node — look for `state` != ready and any
# 0.0.0.0 IPv4 addr (means IPAM hasn't allocated yet)
kubectl -n kube-system exec -it ds/cilium -- cilium endpoint list

# Detail on a single endpoint (policy revision, identity, enforcement mode)
kubectl -n kube-system exec -it ds/cilium -- cilium endpoint get <endpoint-id>

# All identities Cilium has resolved — cross-reference against
# CNP endpointSelectors and toEndpoints/fromEndpoints label matches
kubectl -n kube-system exec -it ds/cilium -- cilium identity list
```

A pod stuck with a stale identity after a label change is the
classic "I updated the CNP selector but nothing changed" trap —
`cilium endpoint get` shows the identity's labels directly; if they
don't match what the pod currently has, force a resync by deleting
the pod (not the CNP).

### Hubble UI and CLI

```sh
# Port-forward Hubble UI (also reachable via its HTTPRoute if the
# gateway path is up — see kubernetes/apps/kube-system/cilium/app/httproute.yaml)
kubectl -n kube-system port-forward svc/hubble-ui 12000:80

# CLI equivalent — live flow tail across the whole cluster
kubectl -n kube-system exec -it ds/cilium -- hubble observe -f

# Scoped to one namespace, denied traffic only (the triage workhorse —
# see CNP troubleshooting below)
kubectl -n kube-system exec -it ds/cilium -- hubble observe \
  --namespace <ns> --verdict DROPPED --last 100
```

## CiliumNetworkPolicy troubleshooting

This cluster's model, in one line: **default-deny per namespace,
additive allow-CNPs punch narrow holes.** Every locked-down namespace
carries the `network-policy/default-deny` Component (deny-all
ingress+egress) plus the `network-policy/baseline` Component (DNS,
apiserver, intra-namespace, monitoring-scrape, host-probes) plus
per-app overlays for anything else. See the component READMEs linked
above for exactly what each baseline policy covers, and the rollout
plan for the full app-pattern catalog (ingress-only web app, web
app + CNPG database, CNPG + Garage backup, camera ingress,
VPN-routed app, etc.) — don't hand-roll a pattern that already exists
there.

### Finding why a flow is dropped

1. **Confirm it's actually a policy drop, not something else**
   (DNS failure, wrong Service selector, upstream app crash). Rule
   these out first — `hubble observe --verdict DROPPED` only shows
   *policy* drops; a `kubectl exec ... curl` connect refused or DNS
   NXDOMAIN is a different failure class.
2. **Tail Hubble scoped to the destination namespace:**

   ```sh
   kubectl -n kube-system exec -it ds/cilium -- hubble observe \
     --namespace <ns> --verdict DROPPED -f
   ```

   Reproduce the failing request from the source pod while this is
   running.
3. **Read the flow's policy attribution.** With
   `hubble-network-policy-correlation-enabled` on (cluster default),
   each denied flow response carries which policy *would* have
   allowed it and why it didn't match — check `egress_allowed_by` /
   `ingress_denied_by` in the flow's verbose output
   (`hubble observe -o json` for the full field set).
4. **Check the reason code.** `hubble_drop_total{reason=...}` and
   the CLI's printed reason distinguish `POLICY_DENIED` from
   dataplane-level drops (e.g. `INVALID_SOURCE_MAC`,
   `MISSED_TAIL_CALL`) that have nothing to do with your CNP.
5. **List the CNPs actually selecting the pod:**

   ```sh
   kubectl get cnp -n <ns> -o yaml | grep -B5 'endpointSelector'
   kubectl -n kube-system exec -it ds/cilium -- cilium policy get
   ```

   Cross-check label selectors against the pod's actual labels
   (`kubectl get pod <pod> -n <ns> --show-labels`) — a selector typo
   is the single most common cause of "the allow rule is right there
   and it's still dropping."

### Audit mode → enforce workflow

Every new CNP in this cluster ships **audit-first**. The annotation
`policy.cilium.io/audit-mode: "enabled"` makes Cilium log
`DROPPED-AUDITED` verdicts in Hubble without actually dropping the
packet — you get full visibility into what a policy *would* block
before it can break anything.

```sh
# Watch for audited-but-not-enforced drops in a namespace mid-rollout
kubectl -n kube-system exec -it ds/cilium -- hubble observe \
  --namespace <ns> --verdict DROPPED --last 200 | grep AUDITED
```

Operationally: land the policy with the annotation, soak (24h for
baseline-only, 48h–2 weeks for default-deny depending on namespace
criticality per the rollout plan), confirm zero unexplained
`DROPPED-AUDITED` flows, then strip the annotation via a
namespace-level kustomize patch (see the `baseline` component
README for the exact patch). **Do not flip a whole Component's
source file** — that changes every namespace using it at once; the
enforce transition is always a per-namespace patch.

If you're debugging a namespace that's already enforcing and
traffic is silently vanishing with no Hubble drop at all, suspect
the L7 DNS proxy path (`toFQDNs` needs the `allow-dns` L7 rule to
have populated Cilium's FQDN cache from an observed query first) —
see the baseline README's "Dropping the L7 DNS proxy per-namespace"
section for the mechanics and the opt-out patch.

## BGP peering and LoadBalancer IPs

This cluster peers Cilium's BGP control plane with the home
router (`brain`) to advertise Service `LoadBalancer` IPs onto the
LAN — no MetalLB, no L2 announcements (`l2announcements.enabled:
false` in the HelmRelease values). The config is the
**three-resource model**:

| Resource | Role |
|---|---|
| `CiliumBGPClusterConfig` | Which nodes run a BGP instance, local ASN, peer list |
| `CiliumBGPPeerConfig` | Per-peer address-family + advertisement selector |
| `CiliumBGPAdvertisement` | What gets advertised (labeled `advertise: bgp`) and to which Services |

Manifests: `kubernetes/apps/kube-system/cilium/config/bgp.yaml`.
The advertisement resource in this cluster uses a deliberately
inverted `NotIn` selector (`fakeSelector NotIn
[will-match-and-announce-all-services]`) to advertise **every**
`LoadBalancer` Service's IP rather than requiring per-Service
opt-in labels — read the selector as "advertise all" rather than a
typical scoped rule.

> The older single-resource `CiliumBGPPeeringPolicy` is retired in
> this cluster (see `.agents/instructions/schema.correction.md`). A
> manifest of that kind in Git is drift, not active config — flag it,
> don't reconcile it as-is.

### Checking peer status

```sh
# BGP peer state — Established is healthy; anything else is down
kubectl -n kube-system exec -it ds/cilium -- cilium bgp peers

# Routes actually being advertised right now
kubectl -n kube-system exec -it ds/cilium -- cilium bgp routes advertised ipv4 unicast

# The CRs themselves — check .status for peering/advertisement conditions
kubectl get ciliumbgpclusterconfig -o yaml
kubectl get ciliumbgppeerconfig -o yaml
kubectl get ciliumbgpadvertisement -o yaml
```

Peer identity in this cluster: the router is referred to as
`<router-hostname>` (`brain`) at `<router-ip>` (LAN gateway address),
peering as `<peer-asn>` against Cilium's `<local-asn>`. Treat the
actual ASNs and IP as network-topology detail owned by
`lovenet-network-configuration` / the network operator persona, not
this repo's public docs.

### LoadBalancer IP pool

```sh
# Pool definition + allocation stats
kubectl get ciliumloadbalancerippool -o yaml

# Which Service holds which LB IP
kubectl get svc -A -o wide | grep LoadBalancer
```

Pool is defined at `kubernetes/apps/kube-system/cilium/config/pool.yaml`
as a single CIDR block. If a new `LoadBalancer` Service comes up
`<pending>` for its external IP, check pool exhaustion first
(`kubectl describe ciliumloadbalancerippool` shows allocated vs
total), then check whether `CiliumBGPAdvertisement`'s selector
actually matches the Service — a Service can hold an allocated IP
that never gets advertised if the advertisement's label selector
was narrowed and no longer matches it.

## Metrics interpretation

Hubble metrics are scraped via the `hubble-metrics` Service
(port 9965) into Prometheus. The enabled metric set (see
`kubernetes/apps/kube-system/cilium/app/values.yaml`) includes
`dns`, `drop`, `tcp`, `flow`, `port-distribution`, `icmp`, and
`http`, with `labelsContext=source_namespace,source_pod` added to
`dns` and `drop` specifically so a query or a drop can be attributed
back to the pod that caused it (the default Hubble metric only
attributes to the observing cilium-agent, which is useless for
per-app triage).

### Policy drops

`hubble_drop_total{reason="POLICY_DENIED"}` is the signal behind
`CiliumPolicyDropsSustained` (warning, >0.5 pkt/s sustained 10m) and
`CiliumPolicyDropBurst` (critical, >200 drops in 2m) — both defined
in
`kubernetes/apps/observability/kube-prometheus-stack/app/prometheusrule-cilium-drops.yaml`,
grouped by `destination_namespace`.

**Known false-positive: ICMP on synchronized restarts.** Both alerts
exclude `protocol="ICMPv4"` from their `rate()`/`increase()`
expressions. A namespace-wide rolling restart (e.g. a batch of image
bumps landing at once) produces a burst of denied ICMPv4 — stale
ARP/neighbor-discovery probes hitting pod IPs that were just
reassigned. No CNP in this cluster permits ICMP ingress at all, so
this traffic is always `POLICY_DENIED` and is never itself the "an
app actually can't reach its dependency" signal — that signal is
always TCP/UDP. If you're staring at a drop-rate graph with an ICMP
spike coinciding with a Renovate batch or a mass pod restart, that's
expected noise, not an incident.

When one of these alerts *does* fire on non-ICMP traffic, treat it
as a real regression: check the most recent CNP-touching merge for
the `destination_namespace` in the alert, then follow the
[CNP troubleshooting](#ciliumnetworkpolicy-troubleshooting) flow
above starting from step 2 (tail Hubble scoped to that namespace).

### Useful ad-hoc queries

```promql
# Drop rate by namespace pair, last 5m
sum by (source_namespace, destination_namespace) (
  rate(hubble_drop_total{reason="POLICY_DENIED", protocol!="ICMPv4"}[5m])
)

# DNS query volume by pod (novel-destination / egress-anomaly base signal —
# see egress_restriction_design.md for how this feeds detection alerting)
sum by (source_namespace, source_pod) (rate(hubble_dns_queries_total[5m]))

# BGP session flap detection (if exported — verify metric name against
# the live cilium-agent /metrics before wiring an alert on it)
cilium_bgp_session_state
```

## Common triage flows

| Symptom | First checks |
|---|---|
| Pod can't reach a Service in another namespace | `hubble observe --verdict DROPPED` scoped to the destination namespace; confirm a CNP in that namespace actually has an ingress rule matching the source's identity/labels, not just that one *exists*. |
| Pod can't reach anything external (egress blocked) | Check for `toFQDNs` without the DNS L7 proxy rule (see [Audit mode → enforce workflow](#audit-mode--enforce-workflow)); check `cilium policy get` for the pod's endpoint to confirm the expected egress CNP is actually selecting it; rule out the perimeter (brain firewalld) as a separate hop — see `egress_restriction_design.md` § egress inventory. |
| BGP session down / route not advertised | `cilium bgp peers` for session state; if not `Established`, this is very likely a `brain`-side config or reachability issue — hand off to the network-operator persona rather than iterating on the Cilium CRs. If `Established` but a specific Service's IP isn't advertised, check the `CiliumBGPAdvertisement` selector against that Service's labels. |
| LB IP stuck `<pending>` | Pool exhaustion (`describe ciliumloadbalancerippool`) first, advertisement selector mismatch second. |
| Everything in a namespace went Unready right after a netpol change | Missing `allow-host-probes` baseline policy — kubelet probes get denied under default-deny without it. Confirm the `baseline` Component is actually included in that namespace's `kustomization.yaml`. |
| Alert fired but the app seems fine | Check `protocol` label on the underlying `hubble_drop_total` series — an ICMP-only burst during a restart is the known false-positive above, not a real gap. |

## What this is NOT

- Not a design or rollout-status document — see the linked docs at
  the top for rationale, phasing, and the app-pattern catalog.
- Not a substitute for the component READMEs — this doc assumes you
  already know what `baseline` and `default-deny` provide and
  focuses on diagnosing them live.
- Not a guide to Istio/mTLS troubleshooting — that's a separate mesh
  layer scoped to `mcp-system` today; see `mtls_rollout_design.md`.
