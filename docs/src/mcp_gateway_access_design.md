# MCP Gateway Access & Identity Design

Status: **Proposal — 2026-10-01.** Supersedes, for *current* consumers, the
scope of the abandoned [per-tool-authz
proposal](mcp_tool_authz_design.md) (whose subject, `opencode`, no longer
exists). That doc is retained as a postmortem and its hard constraints carry
forward here unchanged.
Owner: home-ops
Last updated: 2026-10-01

## TL;DR

The MCP gateway federates every server in `mcp-system` (`kubectl_*`,
`omada_*`, `ha_*`, `netbox_*`, `comfyui_*`, …). "Can a policy give different
clients different tools?" splits into **two separable problems**, and
conflating them is what burned the last attempt:

1. **Authentication gap (cheap, safe — recommended now).** The JWT the
   cluster already mints is only enforced on the *external* hostname path.
   In-cluster clients dial the broker Service directly and present **no
   identity at all**. Today `open-webui` reaches the full federated tool
   surface with `auth_type: none`. This is an authn hole, not an authz one,
   and closing it needs no Authorino and no wasm-shim.

2. **Per-tool authorization (expensive, previously caused an outage).**
   Giving *different* clients *different* tools requires Kuadrant/Authorino
   `AuthPolicy`. An earlier implementation
   ([postmortem](mcp_tool_authz_design.md)) attached that policy to the
   shared Istio Gateway and **took the whole MCP path down**. It is viable
   **only on a dedicated Istio `Gateway` object**, and for today's consumer
   set it is probably still disproportionate.

**Recommendation:** do Decision 1 (close the authn door). Treat Decision 2
as gated behind a concrete need for cryptographic, per-client tool
differentiation that does not exist today. Leave the kagent fleet on direct
access (Decision 3) and harden its governance in-repo instead.

## The load-bearing constraint (from the 2026-08-29 postmortem)

Do not re-derive this; it cost a shared-gateway outage to learn:

> Attaching an `AuthPolicy` to the `mcp-gateway` Istio Gateway makes Kuadrant
> install a fail-closed, **gateway-wide** wasm-shim
> (`oci://quay.io/kuadrant/wasm-shim`). A WasmPlugin on a Gateway workload
> applies to **all its listeners**, and under `FAIL_CLOSE` a failed *fetch*
> 5xxes every request. `sectionName` scopes *matching*, not *fault
> isolation* — an additive listener on the same Envoy shares the failure
> domain. Isolation requires a **separate Istio `Gateway` object** (its own
> Envoy).

Any authz design that attaches a policy to the Envoy serving `mcp`/`mcps` is
wrong by construction, regardless of `sectionName`.

## Current consumer map (2026-10-01)

| Consumer | Path to tools | Identity / enforcement |
|---|---|---|
| `open-webui` (collab) | **direct → broker Service** `mcp-gateway.mcp-system:8080/mcp` | **`auth_type: none`** — no token, full surface |
| Claude Code laptop shell | external → `mcp.${SECRET_DOMAIN}` → envoy (JWT) → broker | JWT **authn only**, full surface |
| external clients (cloudflared) | → envoy (JWT) → broker | JWT **authn only**, full surface |
| `hermes` (ai) | gateway host | dead (`replicas: 0`) — ignore |
| **kagent agents** (ai, `part-of=kagent`) | **direct → each MCP server** `:port`, bypass gateway | per-agent `toolNames` allowlist (**app config**) + L4 CNP |
| kagent controller | direct → each server (reconcile) | L4 CNP only |
| `network-drift` CronJob (ai) | direct → netbox/omada/github/notify | L4 CNP + its own egress pin |

Two shapes of problem: gateway users have **authn but no authz** (full tool
surface once admitted), and the kagent tier **bypasses the gateway
entirely**, with tool scope living in `toolNames` — configuration no central
policy can see or audit. This mirrors the old `opencode` "config, not
enforcement" point: a session/CR can widen its own allowlist.

## Decision 1 — close the authentication side door (recommended)

The JWT `SecurityPolicy` (`securitypolicy.yaml`) binds only to the
`mcp-gateway-internal` HTTPRoute — the external `mcp.${SECRET_DOMAIN}` path
through envoy. The CNP `mcp-gateway-istio-allow` deliberately permits
`fromEntities: cluster` on `:8080`, so in-cluster clients reach the broker
Service directly, below the JWT. `open-webui` does exactly that with
`auth_type: none` (helmrelease.yaml). **The JWT you deploy is trivially
bypassed by anything in-cluster.** Tool ACLs are theater until this is shut.

Options, lowest-risk first:

- **1a — route in-cluster clients through the enforced hostname.** Point
  `open-webui` (and any future in-cluster client) at
  `https://mcp.${SECRET_DOMAIN}/mcp` with a bearer token instead of the bare
  broker Service. Reuses the existing envoy `SecurityPolicy`; no new
  components. `open-webui` can carry a token the same way the laptop shell
  does (reuse `mcp-gateway-jwt-current`, or mint it its own client — see
  Decision 2 identities).
- **1b — tighten the CNP** so the broker/istio Service only accepts traffic
  from the envoy gateway, not `fromEntities: cluster`. Forces every client
  onto the enforced path. More blast radius (any client still on the bypass
  breaks at once) — stage behind 1a.

This is pure authentication: it proves *who* is calling, not *what* they may
call. No Authorino, no wasm-shim, no dedicated gateway. It removes the
largest hole and is a prerequisite for Decision 2 having any meaning.

## Decision 2 — per-tool authorization (gated behind a real need)

This is the "different clients, different tools" ask. The mechanism works;
the deployment shape is the hazard.

### Mechanism (unchanged from the postmortem)

The MCP Router extracts the tool name from the JSON-RPC body and sets
`x-mcp-toolname` **itself** (the client cannot forge it). Authorino
evaluates a predicate against the caller's token claims:

```yaml
authorization:
  tool-access-check:
    patternMatching:
      patterns:
        - predicate: |
            request.headers['x-mcp-toolname'] in (… caller's allowed tools …)
```

`tools/list` filtering is enforced by the same decision via an ES256-signed
"wristband" (`x-authorised-tools`) that Authorino issues and the broker
validates — so a restricted caller cannot even enumerate tools it may not
call.

### Prerequisites specific to this cluster

1. **Enable Kuadrant/Authorino.** Still disabled (`# - kuadrant` in
   `apps/kustomization.yaml`), re-disabled in #13889. Enabling the operator
   alone attaches no policy and is safe; it is the *policy attachment* that
   is dangerous.
2. **A dedicated Istio `Gateway` object** for the authenticated path — its
   own Envoy, so a failed wasm-shim fetch cannot 5xx `mcp`/`mcps`. Not an
   additive listener on the shared gateway (that is the exact 2026-08-29
   failure).
3. **Preload the wasm image into ZOT and pin by digest.** The proximate
   outage trigger was a missing CNP egress for the gateway pod's OCI pull to
   `quay.io:443`. A preloaded, digest-pinned image removes the runtime fetch
   dependency entirely.
4. **Multiple identities.** Today the rotator mints **one** token from one
   Authelia client (`mcp-gateway`), shared by every consumer — you cannot
   differentiate clients that are all the same identity. Give each consumer
   class its own Authelia OIDC client (`mcp-openwebui`, `mcp-claude-laptop`,
   …) so tokens carry a distinct `azp`. Clone the proven
   `mcp-gateway-jwt-rotator` CronJob per client.

### Where the ACL lives

Keep the tool→identity map **in the `AuthPolicy` (GitOps-reviewable)**, not
in an IdP role model. Authelia asserts *which* client via `azp`; Authorino
decides *which tools*. This avoids standing up Keycloak-style client/role
mapping for a handful of homelab consumers:

```yaml
# Illustrative — targetRef MUST be the dedicated gateway, never mcp/mcps.
authorization:
  tool-acl:
    opa:
      rego: |
        client := input.auth.identity.azp
        tool   := input.context.request.http.headers["x-mcp-toolname"]
        allow  { startswith(tool, acl[client][_]) }
        acl := {
          "mcp-openwebui":     ["ha_get_", "music_assistant_", "paperless_get_", "memory_search"],
          "mcp-claude-laptop": [""],   # unrestricted; the operator drives it
          "mcp-external":      ["time_", "search_searxng_"],
        }
```

### Why this is probably not warranted today

The postmortem's second lesson was that the effort was **disproportionate** —
built to constrain one agent that also had a `bash` tool with broad egress,
so it closed the smaller of two doors. The current consumers are no
different: the kagent fleet has `bash` + pinned egress CNPs, and
`open-webui`/the laptop shell are operator-driven. Nobody today needs
*cryptographic, per-client, per-tool* differentiation that the cheaper
controls don't already approximate. Decision 2 should wait for a consumer
that genuinely does (e.g. a semi-trusted external MCP client).

## Decision 3 — the kagent fleet: stay direct, harden governance

Moving the 10 agents behind the gateway+policy would make their tool scope a
reviewed GitOps artifact instead of per-CR `toolNames`. Attractive, but
blocked:

- **Statefulness.** Some backends hand out `Mcp-Session-Id` (streamable-HTTP
  session per pod); the Kuadrant broker cannot federate a stateful server
  (home-ops#14362) — which is exactly why Claude Code → kagent already uses a
  *direct* route. Those backends stay direct regardless of policy.
- **Per-agent identity plumbing.** Each agent + the controller + the cron
  would each need an Authelia client and token rotation. Real work for
  marginal gain over the existing L4 CNP + `toolNames`.

**Recommendation:** keep the kagent tier on direct access. Treat the
`toolNames` allowlists as the control surface and govern them in-repo — e.g.
a review check that an agent CR's `toolNames` stays within its declared tier,
and a periodic audit that the live tool set matches Git. This is consistent
with "Git is canonical" without coupling the fleet to the wasm-shim failure
domain.

## Staged plan

| Stage | Action | Risk | Rollback |
|---|---|---|---|
| 1 | Decision 1a — move `open-webui` onto `mcp.${SECRET_DOMAIN}` with a bearer token | Low | revert helmrelease TOOL_SERVER_CONNECTIONS |
| 2 | Decision 1b — tighten broker/istio CNP to envoy-only ingress | Medium (breaks any remaining bypass client) | revert CNP |
| 3 | kagent governance — `toolNames` tier check + live-vs-Git audit | Low | drop the check |
| 4 | *(only if a real need appears)* Decision 2 on a **dedicated** Istio Gateway, ZOT-preloaded digest-pinned wasm, per-client identities | **High — prior outage** | Flux prune; keep policy off `mcp`/`mcps` at all times |

Stages 1–3 deliver essentially all the realistic security value with none of
the outage risk. Stage 4 is explicitly deferred, not scheduled.

## What this does not solve

Tool-level authz bounds what an agent does **through MCP**. It does not bound
the `bash` tool, which reaches anything the pod's egress CNP allows. The CNP
remains the outer boundary; everything here tightens the inner one. Per
`.agents/instructions/claude-runner-routing.md`, the network-layer answer for
unattended work — read-only surface + scoped RBAC, no mutating broker egress
— is the primary control and is not replaced by any of the above.

## Open questions

1. **Decision 1a token for `open-webui`:** reuse the shared
   `mcp-gateway-jwt-current`, or mint `open-webui` its own client now so the
   identity split exists before any authz work?
2. **Does any in-cluster client other than `open-webui` ride the bypass?**
   Audit before Stage 2 flips the CNP.
3. **`x-mcp-toolname` attribute path** under the current mcp-gateway build —
   confirm against the running broker before trusting any Stage 4 predicate
   (upstream reference abstracts it behind Keycloak role mapping).

## References

- [MCP Tool-Level Authorization Design Proposal — ABANDONED (postmortem)](mcp_tool_authz_design.md)
- [Advanced authentication and authorization for MCP Gateway](https://developers.redhat.com/articles/2025/12/12/advanced-authentication-authorization-mcp-gateway)
- [Kuadrant MCP Gateway request flows](https://docs.kuadrant.io/dev/mcp-gateway/docs/design/flows/)
- Prior effort: #13841 (enable operator) · #13885 (Stages 2–4) · #13887 (revert) · #13889 (re-disable)
