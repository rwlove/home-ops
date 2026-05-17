# network-policy/default-deny

Reusable kustomize Component providing the namespace-wide
`default-deny` `CiliumNetworkPolicy`. **This component locks down
the namespace.** Pair it with `network-policy/baseline` + per-app
allow overlays; without those, every pod loses all ingress and
egress.

## What this provides

One CNP, `default-deny`, with `endpointSelector: {}` and
`ingressDeny: [{}]` + `egressDeny: [{}]`. Cilium 1.14+ explicit
deny primitive; relies on `enable-non-default-deny-policies=true`
(set cluster-wide in `cilium-config`).

## How a namespace includes it

In the namespace's `kustomization.yaml`, alongside the baseline
component:

```yaml
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: <ns>
components:
  - ../../components/network-policy/baseline
  - ../../components/network-policy/default-deny
resources:
  - ./namespace.yaml
  # ...
```

Adjust the relative path to match the namespace's actual depth.

## Pre-flight before adding to a new namespace

The baseline + per-app overlays must cover every legitimate flow
in the namespace, or those flows will be denied the moment this
component lands. Minimum coverage:

- `allow-dns` (baseline) — pods can resolve names
- `allow-apiserver` (baseline) — pods using kube-clients work
- `allow-intra-namespace` (baseline) — sidecar/init/companion deploys talk
- `allow-monitoring-scrape` (baseline) — Prometheus can scrape `/metrics`
- `allow-host-probes` (baseline) — kubelet liveness/readiness/startup probes work
- Pattern A overlay (per-app) — Envoy Gateway → ingress-served apps
- Pattern B/C/etc. overlays per the rollout plan for DB / S3 / external API needs

See `docs/src/networkpolicy_rollout_plan.md` for the full pattern
catalog and which overlays each app needs.

## Audit-mode → enforce-mode

This component intentionally has **no** `policy.cilium.io/audit-mode`
annotation. Adding it lands the namespace in enforce mode immediately.

For the audit-mode-first rollout pattern (per
`networkpolicy_rollout_plan.md` Decision #3), either:

1. **Per-pod audit (canary):** annotate one pod with
   `policy.cilium.io/audit-mode: enabled` *before* this component
   ships, watch Hubble for 48h, then remove the annotation.
2. **Per-namespace patch:** add a `patches:` block in the namespace's
   kustomization that adds the annotation to this CNP, then remove
   the patch when the audit window passes clean.

The canary path is what the rollout plan codifies. The patch path
is the escape valve.

## What this is NOT

- Not for `kube-system` or `flux-system` — explicitly out of scope.
- Not safe to add without the baseline + per-app overlays. Adding
  this component alone breaks every pod in the namespace.
- Not reversible by Cilium's policy ordering — this CNP composes
  with allows via OR semantics, but if no allow matches a flow,
  the flow is dropped.
