---
name: add-mcp-server
description: Scaffold an MCP server under kubernetes/apps/mcp-system/
---

# Add MCP Server

MCP servers under `mcp-system/` follow a tighter pattern than generic
apps because they're registered with the in-cluster MCP gateway via a
sidecar `MCPServerRegistration` CR. Use this skill instead of `add-app`
when adding to `mcp-system/`.

Canonical references in the repo:

- `kubernetes/apps/mcp-system/mealie-mcp/`
- `kubernetes/apps/mcp-system/paperless-mcp/`
- `kubernetes/apps/mcp-system/grafana-mcp/`
- `kubernetes/apps/mcp-system/music-assistant-mcp/` — the **cross-namespace**
  case (backend serves its own MCP mount from another namespace). Read the
  "Cross-namespace backend" section below before copying it.

## Layout

```text
kubernetes/apps/mcp-system/<app>/
├── ks.yaml                       # 2 Kustomizations: app + mcpserverregistration
├── app/
│   ├── kustomization.yaml        # uses components/repos/app-template
│   ├── helmrelease.yaml
│   └── externalsecret.yaml       # if upstream needs an API key
└── mcp/
    ├── kustomization.yaml
    └── mcpserverregistration.yaml
```

## Workflow

### Step 1: Collect details

1. **App name** — `<svc>-mcp` (e.g., `mealie-mcp`)
2. **Upstream image** — `repository` and a sha-pinned tag
3. **Backend service URL** — usually a cluster-local Service:
   `http://<svc>.<ns>.svc.cluster.local:<port>`
4. **Container HTTP port** — what the MCP server listens on
5. **Tool prefix** — short identifier the gateway uses to namespace
   tools (e.g., `mealie_`, `paperless_`)
6. **Secret needs** — if upstream requires an API key/token, note
   which 1Password item key

### Step 2: Generate files

---

**`ks.yaml`** (two Kustomizations: app + registration)

```yaml
---
# yaml-language-server: $schema=https://raw.githubusercontent.com/fluxcd-community/flux2-schemas/main/kustomization-kustomize-v1.json
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: &app <app>
spec:
  commonMetadata:
    labels:
      app.kubernetes.io/name: *app
  targetNamespace: mcp-system
  path: ./kubernetes/apps/mcp-system/<app>/app
  interval: 1h
  timeout: 5m
  prune: true
  wait: false
  sourceRef:
    kind: GitRepository
    name: home-ops-kubernetes
    namespace: flux-system
  dependsOn:
    - name: mcp-gateway
      namespace: mcp-system
  retryInterval: 2m
---
# yaml-language-server: $schema=https://raw.githubusercontent.com/fluxcd-community/flux2-schemas/main/kustomization-kustomize-v1.json
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: &app <app>-mcpserverregistration
spec:
  commonMetadata:
    labels:
      app.kubernetes.io/name: *app
  targetNamespace: mcp-system
  path: ./kubernetes/apps/mcp-system/<app>/mcp
  interval: 1h
  timeout: 5m
  prune: true
  wait: false
  sourceRef:
    kind: GitRepository
    name: home-ops-kubernetes
    namespace: flux-system
  dependsOn:
    - name: <app>
      namespace: mcp-system
  retryInterval: 2m
```

---

**`app/kustomization.yaml`**

```yaml
---
# yaml-language-server: $schema=https://json.schemastore.org/kustomization
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
components:
  - ../../../../components/repos/app-template
resources:
  - ./helmrelease.yaml
  # - ./externalsecret.yaml   # add if secrets needed
```

---

**`app/helmrelease.yaml`**

```yaml
---
# yaml-language-server: $schema=https://raw.githubusercontent.com/fluxcd-community/flux2-schemas/main/helmrelease-helm-v2.json
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: <app>
spec:
  chartRef:
    kind: OCIRepository
    name: app-template
  interval: 1h
  values:
    defaultPodOptions:
      securityContext:
        runAsGroup: 1000
        runAsNonRoot: true
        runAsUser: 1000
    controllers:
      <app>:
        annotations:
          reloader.stakater.com/auto: "true"
        pod:
          annotations:
            sidecar.istio.io/inject: "true"
        containers:
          app:
            image:
              repository: <upstream-image>
              tag: <tag@sha256:digest>
            env:
              <BACKEND>_URL: <backend-url>
            envFrom:
              - secretRef:
                  name: <app>-secret   # if secrets used
            resources:
              requests:
                cpu: 10m
                memory: 128Mi
              limits:
                memory: 256Mi
            securityContext:
              allowPrivilegeEscalation: false
              readOnlyRootFilesystem: true
              capabilities: {drop: ["ALL"]}
              seccompProfile:
                type: RuntimeDefault
    service:
      app:
        controller: <app>
        ports:
          http:
            port: <port>
    route:
      app:
        hostnames:
          - "<app>.mcp.local"
        parentRefs:
          - name: mcp-gateway
            namespace: mcp-system
            sectionName: mcps
        rules:
          - matches:
              - path:
                  type: PathPrefix
                  value: /mcp
            backendRefs:
              - identifier: app
                port: <port>
```

Watch out for these gotchas (all from real outages in this repo):

- **UID mismatch**: if the upstream image's nonroot user is UID 999 but
  you set `runAsUser: 1000`, writes into the image's WORKDIR will fail
  with EACCES. Match the image, or move writable paths to a `tmp`
  emptyDir.
- **Read-only FS + log files**: many MCP servers hardcode a log file
  next to their source. With `readOnlyRootFilesystem: true` you'll get
  EROFS on startup. Fix by setting `workingDir` to a writable path
  (e.g. an emptyDir at `/tmp`) and overriding `command` with an
  absolute path to the entrypoint script.
- **`tag: latest`**: violates the repo's pinning policy. Always pin to
  a published sha-tag.

---

**`app/externalsecret.yaml`** (only if upstream needs credentials)

```yaml
---
# yaml-language-server: $schema=https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/external-secrets.io/externalsecret_v1.json
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: <app>
spec:
  refreshInterval: 5m
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword-connect
  target:
    name: <app>-secret
    creationPolicy: Owner
  dataFrom:
    - extract:
        key: <1password-item-name>
```

---

**`mcp/kustomization.yaml`**

```yaml
---
# yaml-language-server: $schema=https://json.schemastore.org/kustomization
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ./mcpserverregistration.yaml
```

---

**`mcp/mcpserverregistration.yaml`**

```yaml
---
# TODO: apply schema   (mcp.kuadrant.io has no upstream JSON schema yet)
apiVersion: mcp.kuadrant.io/v1alpha1
kind: MCPServerRegistration
metadata:
  name: <svc>-tools
  labels:
    mcp.kuadrant.io/managed: "true"
spec:
  targetRef:
    group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: <app>
  toolPrefix: <prefix>_
```

### Step 3: Wire up

Add both ks.yaml entries (the app and the registration) to
`kubernetes/apps/mcp-system/kustomization.yaml` in alphabetical order.

### Step 4: Verify

- `find kubernetes/apps/mcp-system/<app> -type f` — confirm files.
- After Flux reconciles, the gateway pod should pick up the new
  registration; tools will appear under the configured prefix.
- **Registration `Ready=True` is necessary but NOT sufficient.** It only
  proves the broker's discovery/health path works. Always call one tool
  end-to-end (the invocation path is separate — see below).

## Cross-namespace backend (backend outside mcp-system)

The default pattern above assumes a dedicated MCP-server Deployment **in
mcp-system** whose bjw-s `route:` points at its own same-namespace
Service. Some backends instead serve their MCP endpoint from another
namespace — e.g. `music-assistant`, where the app's FastMCP plugin mounts
`/mcp` straight onto MA's own webserver in `media`. There is no container
in mcp-system; you register the existing Service.

This is the **only** shape that needs the two things below. In-namespace
backends get them for free from `allow-intra-namespace`.

### 1. Raw HTTPRoute + ReferenceGrant

Write a raw `HTTPRoute` in `<app>/app/httproute.yaml` (not a bjw-s
`route:`), attached to `mcp-gateway` / `sectionName: mcps`, path `/mcp`,
with a **cross-namespace** `backendRef` (`name` + `namespace` + `port`).
Because it crosses namespaces, add a `ReferenceGrant` **in the backend's
namespace** permitting `HTTPRoute` (from `mcp-system`) → the Service.
Put that ReferenceGrant in the backend app's own kustomization (so it
lands in the backend namespace via that app's `targetNamespace`), not in
the mcp-system dir. Make the backend's MCP mount path `/mcp` to match the
fleet — the gateway forwards `/mcp` unrewritten.

### 2. CNP holes for BOTH gateway pods

Two different gateway pods open connections to the backend Service, and a
cross-namespace backend must allow **both** — this is the trap that cost
three PRs on music-assistant:

| Pod | Selector | Reaches backend for |
|---|---|---|
| broker | `app.kubernetes.io/name: mcp-gateway` | registration + the 60s health ping (dials the ClusterIP **directly**) |
| istio data-plane | `gateway.networking.k8s.io/gateway-name: mcp-gateway` + `gateway.istio.io/managed: istio.io-gateway-controller` | **every tool invocation** (via the HTTPRoute) |

Allowing only one is a silent half-failure: broker-only → registration
`Ready` but every tool call fails `context canceled`; istio-only →
registration never goes Ready. You need:

- **Egress** (in `<app>/app/cnp-allow.yaml`, mcp-system): two additive
  `CiliumNetworkPolicy` docs — one selecting the broker, one selecting the
  istio pod — each allowing egress to `<backend-ns>`/`<backend>:<port>`.
- **Ingress** (in the backend app's own CNP): one rule with **two**
  `fromEndpoints` (broker + istio pod, both `io.kubernetes.pod.namespace:
  mcp-system`) to `<port>`.

If a tool call fails, confirm the drop with Hubble on the *istio pod's*
node (the egress SYN drop shows there, not on the backend's node):

```bash
hubble observe --from-pod mcp-system/<istio-pod> --to-namespace <backend-ns> \
  --port <port> --verdict DROPPED --last 100
```

### Auth

The gateway forwards a broker Bearer and the fleet strips it at the
HTTPRoute (`RequestHeaderModifier` remove `Authorization`). If the backend
enforces its own auth, either disable it (rely on the CNP + the fact the
mount is only reachable by the two gateway pods) or inject a real token —
do **not** put a token literal in the HTTPRoute. music-assistant runs the
FastMCP plugin with `require_auth: false`.
