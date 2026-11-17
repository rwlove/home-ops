# MCP backend network policy — the dual-CNP pattern

Read this before scaffolding (or copy-pasting) a new MCP federation
backend's `networkpolicies.allow` block, or before adding a new
upstream app dependency to an existing one. A missing half of this
pattern is invisible at `tools/list` time and only surfaces as every
real `tools/call` connect-failing — exactly the class of bug fixed in
home-ops#16050/#16051/#16053/#16055/#16056 (2026-11-17).

## Why two CNP halves are required

Every backend's HTTPRoute attaches to the `mcps` listener on the
`mcp-gateway` Gateway. The **Istio data-plane pod**
(`mcp-gateway-istio`, labeled
`gateway.networking.k8s.io/gateway-name=mcp-gateway`) dispatches every
real `tools/call` **directly to the backend Service** — it does NOT go
through the `mcp-gateway` broker pod
(`app.kubernetes.io/name=mcp-gateway`). Those are two *different* pod
identities, and Cilium enforces policy on **both** endpoints under
default-deny (egress-allow on the caller, ingress-allow on the
callee). `tools/list` is served from the broker's own aggregated
discovery cache (built via the broker pod's *own* manager connection),
so it never exercises the `mcp-gateway-istio` path — federation can
look completely healthy while every real tool call times out.

**There is no blanket rule for either side.** Both CNPs are
per-destination allowlists.

## The two required halves

1. **Ingress, on the new backend's own CNP** (its
   `networkpolicies.allow.ingress` block, or standalone `cnp-allow.yaml`):
   add a *second* `fromEndpoints` entry (or a second `matchLabels`
   block inside the existing one) alongside the broker-identity
   selector:

   ```yaml
   - matchLabels:
       io.kubernetes.pod.namespace: mcp-system
       gateway.networking.k8s.io/gateway-name: mcp-gateway
   ```

   See `kubernetes/apps/ai/calendar-mcp/app/cnp-allow.yaml` or
   `kubernetes/apps/ai/vikunja-mcp/app/helmrelease.yaml` for the live
   pattern.

2. **Egress, in `kubernetes/apps/mcp-system/mcp-gateway/app/cnp-allow.yaml`**
   (`mcp-gateway-istio-allow`): add a `toEndpoints` entry for the new
   backend, same shape as every existing entry there. **This is the
   half that's easy to forget** — it lives in a different app's
   directory than the one you're scaffolding.

Both halves are additive-only, zero broker/gateway-code touch, and
mechanically reversible (`git revert`).

## The next-hop trap

If the backend itself calls out to a *third* app (e.g. an
openapi-proxy bridge calling the real upstream service it fronts),
that third app's **own** ingress CNP needs a selector admitting the
backend's identity too — the exact same pattern, one hop deeper. This
bit `cooter-mcp`→lubelogger, `gordon-mcp`→kitchenowl, and
`tim-mcp`→wardrowbe (home-ops#16055): the gateway↔backend hop was
fixed, which only *exposed* that the backend↔real-app hop had never
been wired at all. Check every app the new backend's env vars point
at, not just the gateway hop.

## A retired false belief — do not reintroduce

Several backends shipped with a comment reading `No istio-egress
(dead, #15834)`. **That comment was wrong**, not a design decision —
PR #15834 only removed a *different*, already-deprecated
`fromEntities: cluster` bypass rule on the gateway's public listener;
it said nothing about the egress half of this pattern. Dual-CNP egress
was always required for every backend reached by `mcp-gateway-istio`.
If you see this comment (or anything asserting istio-egress isn't
needed) anywhere else, it's drift — fix it, don't copy it forward.

## What this is NOT

- Not a reason to add a blanket/wildcard rule on either side. Keep
  both halves per-destination, matching every existing entry's shape.
- Not relevant to same-namespace (`mcp-system`-internal) backends —
  those are covered by the `allow-intra-namespace` baseline on both
  legs already.
