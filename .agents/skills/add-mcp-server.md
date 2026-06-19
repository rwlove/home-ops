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
