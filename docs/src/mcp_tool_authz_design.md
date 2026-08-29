# MCP Tool-Level Authorization Design Proposal

Status: **Proposal — research only, not implemented.** Prerequisite for
running any agent unattended against the MCP gateway.
Owner: home-ops
Last updated: 2026-07-26

> **Update 2026-07-26 — this is no longer a prerequisite for *using* the
> gateway, only for *enforcing* restrictions on it.**
>
> It was previously believed the gateway could not be used at all with a
> 65k-context model, because its `tools/list` is 1,266 tools / 1.34 MB
> ≈ 334k tokens. Measurement showed opencode applies its `tools` filter
> **client-side, before the model call** — an allowlisted agent sends ~12k
> tokens of schemas, and a filtered agent's request measured 18.8k input
> tokens with the entire gateway attached.
>
> **However, enabling it in-cluster on 2026-07-26 still failed** and was
> reverted the same day. The 18.8k figure came from a local `--pure` run
> with plugins disabled. In-cluster the `oh-my-openagent` plugin injects
> six further MCP servers, and the baseline was already 53-57k input tokens
> before any gateway tools; ~12k of allowlisted kubectl pushed it past
> `max_model_len` and vLLM returned 400. The context budget must be cut at
> the baseline before the gateway can be enabled at any allowlist size.
>
> Nothing in the threat model below changes. The allowlist is a
> context-budget control that happens to narrow reach; a session can still
> lift it. Everything this document says about unattended agents still
> applies, and the in-cluster server remains Plan C (interactive, Rob-only).

## Problem statement

The MCP gateway federates every MCP server in `mcp-system` — 18
registrations at time of writing, including `kubectl_*`, `omada_*`,
`ha_*`, `netbox_*` and `comfyui_*`. Any client that reaches the gateway
can call **any** federated tool. There is no per-caller restriction.

Today that is tolerable because every MCP client is driven interactively
by Rob. It stops being tolerable the moment an agent runs unattended:
the in-cluster `opencode` server
([`kubernetes/apps/ai/opencode`](https://github.com/rwlove/home-ops/tree/main/kubernetes/apps/ai/opencode))
runs a `bash` tool and has egress to the gateway, so a single
mis-planned session can mutate the cluster, the network, or Home
Assistant with nothing in the path to stop it.

The client-side gate in `opencode.json`:

```json
"tools": { "lovenet-gateway_*": false }
```

is **configuration, not enforcement**. Any session can lift it at
runtime via `select_tools`. It reduces accidental tool use; it does not
bound a compromised or badly-steered agent.

HOMELAB-SPEC Layer 4 anticipates a guardian mode that gates destructive
operations through a queue, but
[`orchestration_substrate.md`](orchestration_substrate.md) records that
the queue substrate does not exist yet. Until it does, tool-level authz
at the gateway is the only enforcement point available.

## What the gateway already supports

Kuadrant's MCP gateway has a real tool-level authorization story. Three
mechanisms, easy to confuse — only one of them is a security boundary:

| Mechanism | Set by | Boundary? |
|---|---|---|
| `tools: {"lovenet-gateway_*": false}` in `opencode.json` | the client | **No** — client config |
| `MCPVirtualServer.spec.tools` selected via `x-mcp-virtualserver` header | the client picks the header | **No** — client chooses its own view |
| `AuthPolicy` matching `x-mcp-toolname` | the **MCP Router** sets the header | **Yes** |

The third is the one that matters. The router extracts the tool name
from the JSON-RPC body and sets `x-mcp-toolname` itself, so the client
cannot forge it. Authorino then evaluates a CEL predicate against the
caller's token claims:

```yaml
authorization:
  'tool-access-check':
    patternMatching:
      patterns:
        - predicate: |
            request.headers['x-mcp-toolname'] in (...token claims...)
```

`tools/list` filtering is enforced by the same decision, via an
ES256-signed "wristband" header (`x-authorised-tools`) that Authorino
issues and the broker validates — so a restricted caller cannot even
enumerate tools it may not call.

## Blocker: the policy stack is not installed

`AuthPolicy` is **not a known resource type in this cluster**:

```console
$ kubectl get authpolicy -A
error: the server doesn't have a resource type "authpolicy"
```

Only the `mcp.kuadrant.io` CRDs are present (`MCPGatewayExtension`,
`MCPServerRegistration`, `MCPVirtualServer`). The Kuadrant operator and
Authorino — which provide `AuthPolicy` and the authorization service —
are not deployed.

The operator **is** staged in-repo at
[`kubernetes/apps/kuadrant/kuadrant-operator`](https://github.com/rwlove/home-ops/tree/main/kubernetes/apps/kuadrant)
(chart `kuadrant-operator` 1.5.2 — an umbrella pulling in authorino,
limitador, and dns-operator) but is commented out of the top-level
kustomization:

```yaml
  - collab
  # - kuadrant
  - databases
```

Git history shows an `archive-kuadrant` commit that was later reverted,
leaving the app present but disabled. **No rationale is recorded**, and
per the repo's suspend/disable convention this should not be re-enabled
without Rob's explicit instruction. Recovering that rationale is step 0
— if Kuadrant was disabled because it conflicted with Envoy Gateway or
Istio, this whole design needs rethinking.

## The migration hazard

`AuthPolicy` attaches to a Gateway listener:

```yaml
spec:
  targetRef:
    group: gateway.networking.k8s.io
    kind: Gateway
    name: mcp-gateway
    sectionName: mcp
```

and applies to **all** traffic arriving at that listener — including
in-cluster traffic, because in-cluster clients address the istio gateway
Service directly (`mcp-gateway-istio.mcp-system.svc.cluster.local:8080`).

That is the hazard. Current in-cluster MCP clients send **no identity at
all**: `langgraph-agents` uses a bare `MCP_GATEWAY_URL` with no token,
and the CNP `mcp-gateway-istio-allow` deliberately permits
`fromEntities: cluster` on 8080 for exactly this. Attaching an
authenticating AuthPolicy to the `mcp` listener would break every one of
them at once.

The `mcp-gateway` Gateway already has two listeners (`mcp` and `mcps`),
which makes a non-breaking path available.

## Proposed approach

Staged, each stage independently revertible.

### Stage 0 — recover the Kuadrant rationale

Determine why `# - kuadrant` is commented out. If it was cost,
complexity, or "not needed yet", proceed. If it was a conflict with
Envoy Gateway or Istio, stop and redesign. **Rob's call; not an agent
decision.**

### Stage 1 — enable Kuadrant + Authorino

Uncomment the app, let it reconcile into the existing `kuadrant`
namespace (manifest and baseline network policy already exist). Verify
`AuthPolicy` and `AuthConfig` register and that no existing traffic
changes — installing the operator alone attaches no policy.

Rollback: re-comment, Flux prunes.

### Stage 2 — dedicated authenticated listener

Add a listener (e.g. `mcp-authz`) to the `mcp-gateway` Gateway and
attach the `AuthPolicy` with `sectionName: mcp-authz`. Existing clients
stay on `mcp` and are untouched.

This is the key decision: **an additive listener rather than a policy on
the shared one.** Slightly more moving parts, but it means a mistake in
the policy cannot take down `langgraph-agents`, Open WebUI, or Rob's own
Claude Code sessions.

### Stage 3 — identity for opencode

Create an Authelia OIDC client for opencode and mint tokens with the
client-credentials flow. The rotation machinery already exists and is
proven: `mcp-gateway-jwt-rotator` is a CronJob that trades client
credentials for a token and writes it to a Secret. Clone the pattern
rather than inventing one.

The token's claims carry the allowed tool list that the AuthPolicy CEL
predicate matches against `x-mcp-toolname`.

### Stage 4 — cut opencode over and verify negatively

Point opencode's `mcp.lovenet-gateway.url` at the authenticated listener
with the bearer token.

Verification must be **negative**, not just positive:

- an allowed read-only tool (e.g. `kubectl_get_pods`) succeeds
- a mutating tool (e.g. `kubectl_kubectl_apply`) is **denied**, and is
  denied even when the session explicitly calls `select_tools` to enable
  it
- `tools/list` does not enumerate the denied tools
- `langgraph-agents` and Open WebUI still work unchanged

The second bullet is the whole point of the exercise. If a session can
still reach a mutating tool by lifting its own client-side gate, nothing
has been gained.

#### Enumeration test (the wristband)

The third bullet — `tools/list` omits denied tools — is enforced by the
ES256 "wristband" (`x-mcp-authorized` header) and must be tested
explicitly, because the invocation gate and the enumeration gate are
independent code paths in the broker. Invocation denial passing does
**not** imply enumeration filtering works.

Run all three from a pod that can reach the istio Service, with the
opencode bearer token:

```sh
TOKEN=$(kubectl -n mcp-system get secret mcp-gateway-opencode-jwt-current \
  -o jsonpath='{.data.token}' | base64 -d)
URL=http://mcp-gateway-istio.mcp-system.svc.cluster.local:8080/mcp

# tools/list on the AUTHENTICATED listener (Host selects mcp-authz).
curl -s "$URL" -H "Host: mcp-authz.mcp.local" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  | tee /tmp/authz.json | jq -r '.result.tools[].name' | sort > /tmp/authz-tools.txt
```

Assertions (the test passes only if all hold):

1. **Returned set equals the allowlist.** `/tmp/authz-tools.txt` must equal
   the 31 prefixed real-tool names the wristband `allowed-capabilities`
   map enumerates (19 `kubectl_*` + 6 `prom_*` + 6 `time_*` reads) — the
   same 31 the AuthPolicy `tool-access-check` predicate allows minus its
   two meta-tools. `discover_tools` / `select_tools`
   are broker-native meta-tools with no upstream `MCPServerRegistration`,
   so the broker cannot map them to a server and they remain listed;
   allow for them in the diff. `diff <(sort /tmp/authz-tools.txt) <(sort
   allowlist.txt)` should show only those two meta-tools, nothing from
   `omada_*`, `ha_*`, `arr_*`, `comfyui_*`, `kubectl_kubectl_apply`,
   `kubectl_delete_resource`, etc.
2. **Denied tools are absent, not merely un-callable.** Grep the returned
   set for a known-mutating name (`grep -c kubectl_kubectl_apply
   /tmp/authz-tools.txt` == 0, `grep -c '^omada_' == 0`). This is the new
   property: on the pre-wristband build these names appeared in the list
   (but 403'd on call); they must now be *gone from the enumeration*.
3. **The `mcp` listener is unaffected.** The same `tools/list` sent with
   `Host: mcp.example.com` (the public listener, no wristband) must still
   return the full ~1300-tool catalogue — proving the wristband filtering
   is scoped to the authenticated path and did not leak onto the shared
   listener. `wc -l` on that path's tool names ≫ 31.

Negative control for the fail-closed edge: a request to `mcp-authz` with a
**malformed** `x-mcp-authorized` header (or a token the broker's public
key can't verify) must return an **empty** tool list, not the full one
(broker `applyAuthorizedCapabilitiesFilter` returns `[]` on validation
failure). This confirms a forged wristband cannot widen enumeration.

## Proposed initial allowlist for opencode

Read-only to start, matching the credential posture already chosen for
its GitHub access (read-only deploy keys):

- `kubectl_get_*`, `kubectl_describe`, `kubectl_get_logs`
- `prom_*` (all read-only)
- `discover_tools`, `select_tools`, `time_*`
- explicitly excluded: every `omada_*`, `ha_*` mutation, `kubectl_apply`
  / `patch` / `delete` / `scale` / `rollout`, all `arr_*`

Widening the list later is a one-line policy change; starting wide and
narrowing after an incident is not.

## Open questions

1. **Why is Kuadrant disabled?** Blocks everything else.
2. **Does Authorino coexist cleanly with Envoy Gateway's `SecurityPolicy`
   extAuth?** The cluster already runs Authelia extAuth on the Envoy
   gateways; Kuadrant/Authorino would run on the Istio gateway in
   `mcp-system`. They should not interact, but this needs confirming.
3. **Do the other in-cluster clients eventually get identities too?**
   The end state is every MCP client authenticated. This proposal only
   moves opencode, leaving the `mcp` listener unauthenticated.
4. **Token lifetime vs. long agent sessions.** The rotator's 7-day
   lifespan with daily refresh suits shell sessions; a long-running
   server may need refresh-on-401 handling in the client.

## What this does not solve

Tool-level authz bounds what an agent can do **through MCP**. It does
not bound the `bash` tool, which can reach anything the pod's egress
policy allows. The CNP remains the outer boundary; this proposal
tightens the inner one.

## References

- [Advanced authentication and authorization for MCP Gateway](https://developers.redhat.com/articles/2025/12/12/advanced-authentication-authorization-mcp-gateway)
- [Kuadrant MCP Gateway request flows](https://docs.kuadrant.io/dev/mcp-gateway/docs/design/flows/)
- [Orchestration substrate](orchestration_substrate.md) — why the guardian queue does not exist yet
- [Task queue substrate design](task_queue_substrate_design.md)
