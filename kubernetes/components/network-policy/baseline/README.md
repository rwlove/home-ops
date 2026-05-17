# network-policy/baseline

Reusable kustomize Component providing the four baseline
`CiliumNetworkPolicy` (CNP) resources every namespace receives as
the first step of the NetworkPolicy rollout. See the full plan:
`docs/src/networkpolicy_rollout_plan.md`.

## What this provides

| Policy | Purpose |
|---|---|
| `allow-dns` | Egress to CoreDNS in `kube-system`. Includes L7 `dns.matchPattern: "*"` which populates the Cilium FQDN cache used by later `toFQDNs:` overlays. |
| `allow-apiserver` | Egress to `reserved:kube-apiserver` entity, port 6443. **Entity selector, not CIDR** — Cilium 1.19 with `policy-cidr-match-mode=""` silently drops apiserver traffic from CIDR-only rules. |
| `allow-intra-namespace` | Pods in the same namespace can talk to each other (ingress + egress). Covers the bjw-s init-container / sidecar / companion-deployment pattern without per-app rules. |
| `allow-monitoring-scrape` | Ingress from the `observability` namespace's Prometheus pod (`app.kubernetes.io/name: prometheus`). |
| `allow-host-probes` | Ingress from `reserved:host` + `reserved:remote-node` so kubelet liveness/readiness/startup probes work after default-deny lands. Without this, every pod under default-deny goes Unready and restarts in a loop. |

All four ship with:

- `enableDefaultDeny.{ingress,egress}: false` — additive only.
  These policies do **not** by themselves trigger default-deny.
  That happens when the separate `default-deny` companion policy
  lands, ~24h later per the rollout plan.
- `policy.cilium.io/audit-mode: "enabled"` — drops are logged as
  `DROPPED-AUDITED` but **not** enforced. This means including the
  component in a namespace is non-disruptive even if a flow these
  policies don't yet cover would be denied later.

## How a namespace includes it

In the namespace's `kustomization.yaml`:

```yaml
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: <ns>
components:
  - ../../../components/network-policy/baseline
resources:
  - ./namespace.yaml
  # ...
```

Adjust the relative path to match the namespace's actual depth.

## Audit-mode → enforce-mode lifecycle

The intended progression for a namespace adopting this component:

1. **PR 1 (this component).** Add the `components:` reference. The
   four allow-policies are created with audit-mode annotations.
   Hubble starts emitting `DROPPED-AUDITED` flows for anything that
   *would* be denied by these rules — but nothing is actually
   denied because every policy is `enableDefaultDeny: false` and
   nothing is denying by default in the namespace yet.
2. **Soak (24h minimum).** Watch Hubble drops dashboard filtered to
   the namespace. Zero `DROPPED-AUDITED` from the baseline
   policies is the expected steady state.
3. **PR 2.** Add app-specific overlays (Pattern A/B/C/etc. per the
   rollout plan) for ingress, cross-namespace DB connections,
   external FQDN egress, etc. Each overlay also carries the
   audit-mode annotation initially.
4. **PR 3 (`default-deny` companion).** Apply the separate
   `default-deny` CNP. **This is when traffic actually starts
   being dropped.** Apply with audit-mode annotation on a single
   pod first, tail Hubble 48h for unexpected drops, expand to
   namespace-wide once clean.
5. **PR 4.** Remove `policy.cilium.io/audit-mode` annotations from
   all CNPs in the namespace. Enforcement is now live.

The component itself does **not** auto-flip. Removing the audit
annotation requires editing the source files in this component (or
overlaying with a patch) — which deliberately changes behavior for
*every* namespace using the component at once. The intended
operational pattern is per-namespace patching: when a namespace is
ready to enforce, the namespace's `kustomization.yaml` adds a
`patches:` block stripping the annotation.

Example per-namespace enforce patch (do not add this to a namespace
until its audit window passes clean):

```yaml
patches:
  - target:
      group: cilium.io
      version: v2
      kind: CiliumNetworkPolicy
      name: ".*"
    patch: |
      - op: remove
        path: /metadata/annotations/policy.cilium.io~1audit-mode
```

## The `default-deny` companion

`default-deny` is intentionally **not** in this component. Bundling
them would defeat the entire audit-mode workflow — namespaces
would default-deny on day one and the audit window would be useless.

The default-deny CNP lives as a separate manifest, applied via a
separate PR in the per-namespace Kustomization, after the baseline
has soaked. See `docs/src/networkpolicy_rollout_plan.md` for the
default-deny policy text (uses `ingressDeny: [{}]` /
`egressDeny: [{}]`).

## What this is NOT

- Not a default-deny. Adding this component does not lock down the
  namespace.
- Not a substitute for app-specific egress rules. Real apps need
  Pattern A/B/C/etc. overlays in addition to the baseline.
- Not appropriate for `kube-system` or `flux-system`. Those
  namespaces are explicitly out of scope (rollout plan decision).
