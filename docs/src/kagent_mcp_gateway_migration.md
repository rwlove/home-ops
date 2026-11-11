# kagent → Kuadrant mcp-gateway migration plan

**Status:** In progress — pilot Step A built (auth+transport foundation) · **Created:** 2026-11-11 · **Tracking:** home-ops#15735

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
| Gateway auth | **Authelia bearer via the enforced hostname** — kagent reuses the existing `mcp-gateway` Authelia OIDC client (client-credentials, `aud=mcp-gateway`) through its **own rotator** (mirrors the mcp-system / open-webui rotators) into an ns-`ai` secret, injected as `Authorization: Bearer …` via `RemoteMCPServer.spec.headersFrom`. All clients dial `https://mcp.${SECRET_DOMAIN}/mcp`; in-cluster direct-to-broker is closed (`mcp_gateway_access_design.md` Decision 1b) | No Authelia config change, fewest new parts, correct authn; the existing route-scoped Envoy `SecurityPolicy` already validates `aud=mcp-gateway` and is **not** the fail-closed Authorino path. Actor-JWT (Substrate IdP as a second provider) was the earlier pick but needs a gateway-side JWT-provider change and the Substrate IdP issuer wired in; the bearer path reuses proven machinery. A dedicated `mcp-kagent` client is the access-design doc's Decision 2 (deferred) |
| Scope | **Pilot-then-expand**, pilot split into **Step A (additive foundation, zero agent impact)** then **Step B (reversible agent flip)** | Lowest blast radius; Step A validates auth+transport+discovery before any agent is touched; reversible at each gate |
| SSE backends | **Re-front as streamable-http** | MCP spec deprecated HTTP+SSE for Streamable HTTP; every federated backend is already streamable-http; also fixes pump-mcp's discovery failure |
| Per-tool authz | **Keep per-agent `toolNames` allowlist; defer Authorino** | Authorino `AuthPolicy` on the shared gateway fail-closes cluster-wide (prior outage); real per-tool enforcement needs a dedicated gateway (separate project) |

**Consumption shape:** kagent points `RemoteMCPServer`(s) at the gateway endpoint
(`https://mcp.${SECRET_DOMAIN}/mcp`) and each agent keeps a `tools[]` allowlist —
but rewritten to the gateway's **prefixed** names. Each gateway `RemoteMCPServer`
carries two `headersFrom` entries: `Authorization` (from the rotator secret
`kagent-mcp-gateway-jwt`, key `authorization`, already `Bearer …`) and
`X-Mcp-Virtualserver` (static `mcp-system/<scope>`). Because of the ~940-tool cap,
use a small number of `MCPVirtualServer`-scoped endpoints rather than one giant
endpoint; the pilot starts with a single `kagent` scope (`mcp-system/kagent`) and
grows it as agents migrate.

**Auth plumbing (ns `ai`, mirrors mcp-system/open-webui):**
`ExternalSecret kagent-mcp-gateway-authelia-client` (1P `mcp-gateway-authelia`) →
`CronJob kagent-mcp-gateway-jwt-rotator` (client-credentials grant to
`https://auth.${SECRET_DOMAIN}/api/oidc/token`, `scope=audience=mcp-gateway`) →
secret `kagent-mcp-gateway-jwt` (`token` raw, `authorization` = `Bearer <jwt>`).
A dedicated `CiliumNetworkPolicy` opens the rotator's egress to the public Envoy
gateway; the kagent controller (discovery) and agent pods (invocation) get the
same envoy egress added to their existing CNPs.

**Stays direct (does not federate):** kagent's own `kagent-controller` and
`kagent-tool-server` (stateful / `Mcp-Session-Id`, cannot federate — home-ops#14362).

## Phase 1 pilot — Step A: auth + transport foundation (no agent impact)

All additive; **no agent references the gateway yet**, so the live fleet is
untouched. Validates the entire auth+transport+discovery path before any flip.

1. **Auth plumbing** (ns `ai`): the `ExternalSecret` → rotator `CronJob` → secret
   chain above, plus the rotator egress CNP. (One-time: after merge, trigger the
   rotator once — `create job --from=cronjob/kagent-mcp-gateway-jwt-rotator` — so
   the `kagent-mcp-gateway-jwt` secret exists before the next scheduled run.)
2. **`kagent` `MCPVirtualServer`** (ns `mcp-system`) scoped to `memory_search`
   only — the minimal pilot scope; grows as agents migrate.
3. **Gateway `RemoteMCPServer` `mcp-gateway`** (ns `ai`): `url`
   `https://mcp.${SECRET_DOMAIN}/mcp`, `headersFrom` the bearer + the
   `mcp-system/kagent` virtual-server header. No agent references it.
4. **Controller + agent envoy egress** added to the existing kagent CNPs.
5. **Validate:** the rotator mints a token; `kubectl get rmcps mcp-gateway -n ai`
   reaches **DiscoverySucceeded** exposing `memory_search`. **Bake** here.

**Rollback:** all additive — `git revert`; nothing referenced it.

Also keep a frozen inventory of every agent's current `tools[]` (backend-native)
↔ the gateway's prefixed names, as the Phase-2 rewrite source of truth.

## Phase 1 pilot — Step B: flip one agent (triage-router → memory via gateway)

triage-router is the ideal flip: it uses exactly **one** read-only tool
(`memory-mcp` → `search`), already federated as `memory_search`, so the blast
radius is a single low-risk classifier reading curated memory.

1. **Repoint** triage-router's `tools[]` from `{RemoteMCPServer memory-mcp →
   search}` to `{RemoteMCPServer mcp-gateway → memory_search}`.
2. **Validate:** triage-router discovers `memory_search` via the gateway; a live
   classification that exercises `search` returns correctly; discovery stable for
   ≥1 reconcile cycle. **Bake** before expanding.

**Rollback:** revert the one-line `tools[]` change (git revert); the direct
`memory-mcp` `RemoteMCPServer` + its CNP are untouched until Phase 4.

## Phase 2 — Batched federation + migration (remaining backends)

Migrate in small batches ordered by blast radius, **read-only/low-risk first**,
write-capable and the media-acquisition MCP **last**. For each batch:

1. For any **not-yet-federated** backend (15 of them, incl. the 6 openapi-mcp SSE
   bridges, owl-mcp, and **pump-mcp** — the one backend currently *failing* direct
   discovery because crystaldba/postgres-mcp:0.3.0 is SSE-only, so federating it
   is also its fix): re-front SSE→streamable-http where needed, add the
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

The Authelia bearer is enforced from Step A onward (the gateway's Envoy
`SecurityPolicy` already validates `aud=mcp-gateway` on the enforced hostname), so
there is no later "turn auth on" step. This phase instead: confirm the gateway
rejects an un-authenticated / wrong-`aud` call; keep the per-agent `toolNames`
allowlist as the tool scope; and decide whether to graduate from the shared
`mcp-gateway` client to a dedicated `mcp-kagent` Authelia client (access-design
doc Decision 2) once the fleet is fully migrated.

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
2. **Bearer injection on the actor invocation path** — the novel bit. Controller
   *discovery* egresses from a normal ns-`ai` pod (plain Cilium CNP, like every
   other backend), but *actor invocation* is SNAT'd through the Substrate egress
   gateway (`atenet-egress`), which is where `headersFrom` secret headers are
   injected. Step A validates discovery; **Step B** is the first real test that
   the bearer reaches the gateway on the actor path. If it doesn't, the fallback
   is `tokenPassthrough`/`actorJwt` for invocation. (The gateway auth *mechanism*
   itself is already proven — open-webui consumes the same enforced hostname with
   the same bearer.)
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
