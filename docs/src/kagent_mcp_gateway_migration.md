# kagent → Kuadrant mcp-gateway migration plan

**Status:** Plan (approved model, pilot pending) · **Created:** 2026-11-11 · **Tracking:** home-ops#15735

Migrate the kagent 1.0 agent fleet from dialing MCP servers **directly** (one
`RemoteMCPServer` per backend) to consuming them **through the cluster's Kuadrant
mcp-gateway** (agentgateway broker, ns `mcp-system`). The end-state centralizes
federation, transport, identity, and observability, and collapses the
hand-maintained per-backend CiliumNetworkPolicy fan-out to a single gateway hop.

## Current state

- kagent (controller + 17 agents) dials **27 `RemoteMCPServer`s** directly; each
  agent's `tools[]` allowlist uses **backend-native** tool names.
- The mcp-gateway already federates **19 backends** behind one streamable-http
  endpoint, exposing **prefixed** tool names (`grafana_`, `memory_`, `omada_`,
  `ha_ha_*`, …) via per-backend `MCPServerRegistration.spec.prefix`. **10 of
  kagent's 27** backends are among those already federated.
- Full federated catalog is **~940 tools** — larger than some clients' caps, so
  the gateway uses **`MCPVirtualServer`** scoped tool-subsets (selected by the
  `X-Mcp-Virtualserver` header).
- Gateway auth today: an **Envoy Gateway `SecurityPolicy`** (route-scoped JWT,
  Authelia issuer, aud `mcp-gateway`) on the *external* hostname only; the
  in-cluster broker Service is unauthenticated (CNP-gated). This is **not** the
  Authorino/`AuthPolicy` wasm shim (that one fail-closes the whole gateway — see
  `mcp_tool_authz_design.md`).

## Target model + decisions

| Fork | Decision | Why |
|---|---|---|
| Gateway auth | **Actor-JWT** — kagent sets `RemoteMCPServer.spec.actorJwt.audiences: [mcp-gateway]`; gateway validates the **Substrate IdP** issuer via a second JWT provider | Upstream agentgateway guidance = JWT for service-to-service; uses kagent's native per-agent identity (no bespoke token rotator); route-scoped Envoy `SecurityPolicy` ≠ the fail-closed Authorino path |
| Scope | **Pilot-then-expand** | Lowest blast radius; reversible at each gate |
| SSE backends | **Re-front as streamable-http** | MCP spec deprecated HTTP+SSE for Streamable HTTP; every federated backend is already streamable-http; also fixes pump-mcp's discovery failure |
| Per-tool authz | **Keep per-agent `toolNames` allowlist; defer Authorino** | Authorino `AuthPolicy` on the shared gateway fail-closes cluster-wide (prior outage); real per-tool enforcement needs a dedicated gateway (separate project) |

**Consumption shape:** kagent points `RemoteMCPServer`(s) at the gateway endpoint
and each agent keeps a `tools[]` allowlist — but rewritten to the gateway's
**prefixed** names. Because of the ~940-tool cap, use a small number of
`MCPVirtualServer`-scoped endpoints (e.g. an ops scope, a concierge scope) rather
than one giant endpoint, injected per `RemoteMCPServer` via the
`X-Mcp-Virtualserver` header (`headersFrom`).

**Stays direct (does not federate):** kagent's own `kagent-controller` and
`kagent-tool-server` (stateful / `Mcp-Session-Id`, cannot federate — home-ops#14362).

## Phase 0 — Foundations (no agent impact)

1. **Auth feasibility (confirm):** add the Substrate IdP as a second JWT provider
   on the gateway's in-cluster MCP route — issuer `https://idp.ate-system.svc`,
   `audiences: [mcp-gateway]`, `remoteJWKS` from the IdP's `jwks_uri`. Verify an
   Envoy `SecurityPolicy` can target the in-cluster route (vs. the external
   hostname); if the in-cluster (Istio) path can't take an Envoy SecurityPolicy,
   enforce with an Istio `RequestAuthentication` on the gateway instead. Validate
   a kagent actor-JWT is accepted end-to-end with a throwaway call **before**
   flipping any agent.
2. **Decide the `MCPVirtualServer` scopes** kagent needs (map the 17 agents'
   tool sets → minimal scopes) and author them.
3. Keep a frozen inventory of every agent's current `tools[]` (backend-native) ↔
   the gateway's prefixed names, as the rewrite source of truth.

**Rollback:** nothing changed for agents; drop the added JWT provider + virtual
servers.

## Phase 1 — Pilot: pump-mcp (one backend, arnold + crusher)

pump-mcp is the ideal pilot: it's the one backend currently **failing** direct
discovery (crystaldba/postgres-mcp:0.3.0 is SSE-only; the 1.0 controller's
discovery breaks its SSE endpoint), so the pilot is also the fix.

1. **Re-front** pump-mcp as streamable-http (bridge via `mcp-proxy`, same pattern
   as owl-mcp, or a crystaldba version that serves streamable-http) → `/mcp`.
2. **Federate:** add an HTTPRoute on the gateway's `mcps` listener
   (`pump-mcp.mcp.local` → the Service) + an `MCPServerRegistration` with
   `prefix: pump_` + `targetRef` the route. Confirm `READY=True` and the tool
   appears in the catalog as `pump_*`.
3. **Consume via gateway:** repoint the `pump-mcp` `RemoteMCPServer` at the
   gateway endpoint (+ `actorJwt` + the ops `X-Mcp-Virtualserver` header), and
   rewrite arnold's + crusher's `tools[]` entries to the `pump_`-prefixed names.
4. **Validate:** `RemoteMCPServer` `Accepted=True`; a live `pump_*` tool call from
   arnold succeeds; discovery stable for ≥1 reconcile cycle.
5. **Bake** before expanding.

**Rollback:** revert the agent `RemoteMCPServer`/`tools[]` to the direct dial
(git revert); the direct path + its CNP are untouched until Phase 4.

## Phase 2 — Batched federation + migration (remaining backends)

Migrate in small batches ordered by blast radius, **read-only/low-risk first**,
write-capable and the media-acquisition MCP **last**. For each batch:

1. For any **not-yet-federated** backend (15 of them, incl. the 6 openapi-mcp SSE
   bridges + owl-mcp): re-front SSE→streamable-http where needed, add the
   `mcps` HTTPRoute + `MCPServerRegistration` (+ prefix), confirm `READY=True`.
2. Repoint the agents' `RemoteMCPServer` refs at the gateway + rewrite their
   `tools[]` to prefixed names.
3. Validate discovery + one real tool call per affected agent.
4. Collapse that backend's now-dead direct-dial egress rule only after its traffic
   is confirmed on the gateway.

Batches (suggested): (a) already-federated read tools (grafana/memory/omada/
netbox/github/ha/esphome/comfyui/paperless); (b) ai-ns read tools
(calendar/ghostfolio/health/rick-gonic); (c) the SSE bridges
(cooter/gordon/grocy/homebox/lloyd/tim) + owl; (d) write-capable + the
media-acquisition MCP + notify/pr.

**Rollback:** per-batch git revert; each batch is independently reversible.

## Phase 3 — Auth hardening

Flip every migrated `RemoteMCPServer` to require the actor-JWT (if not done per
backend in Phases 1–2), and confirm the gateway rejects un-authenticated
in-cluster calls. Keep the per-agent `toolNames` allowlist as the tool scope.

## Phase 4 — Teardown + docs

- Remove the per-backend direct-dial egress rules from `ai/kagent/app/cnp-allow.yaml`
  (`kagent-agents-allow`, `kagent-controller-mcp-discovery-allow`) and the matching
  `mcp-system/.../cnp-kagent-read-allow.yaml` ingress fan-out, leaving a single
  kagent→gateway egress/ingress pair.
- Standardize robust packaging: replace any runtime-PyPI-vendored bridges
  (owl-mcp) with pre-baked images in the `containers` repo.
- Update this doc to "Done", update the runbook (home-ops#15735) and memory.

## Risks

1. **Tool-name rewrite across 17 agents** (prefixed names, incl. `ha_ha_*`) — the
   bulk of the work; mis-mapping silently removes a tool. Mitigate: the Phase-0
   frozen inventory + per-agent validation.
2. **Auth path** — if the in-cluster route can't take the Envoy SecurityPolicy,
   fall back to Istio `RequestAuthentication` on the gateway (do **not** use the
   Authorino `AuthPolicy` — fail-closes the shared gateway).
3. **SSE re-fronting** — the openapi-mcp bridges + pump may need a bridge
   container; owl-mcp already proves the `mcp-proxy` pattern.
4. **~940-tool cap** — size the `MCPVirtualServer` scopes so no agent's client
   silently drops tool families.
5. **Partial by construction** — stateful backends stay direct; this is a hybrid
   end-state, not "27 → 1".

## Data classification

Internal. The media-acquisition MCP (HOMELAB-SPEC L2 #2) is referred to generically
throughout and never by its tool names; no secrets, media file names, or restricted
identifiers appear here.
