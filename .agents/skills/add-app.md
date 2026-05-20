---
name: add-app
description: Scaffold a new app-template HelmRelease application in this repo
---

# Add New Application

This skill scaffolds a new application following the conventions in
`kubernetes/apps/`. The app-template chart is shared via the
`components/repos/app-template/` Component — apps consume it through
`chartRef`, not by creating their own `OCIRepository`.

For MCP servers under `kubernetes/apps/mcp-system/`, prefer the
`add-mcp-server` skill — that pattern has a sidecar
`MCPServerRegistration` that this skill does not produce.

## Workflow

### Step 1: Collect Application Details

Ask the user for:

1. **App name** — e.g. `nametag`, `pump`
2. **Namespace** — must already exist under `kubernetes/apps/<namespace>/`
3. **Image repository** — e.g. `ghcr.io/<owner>/<repo>`
4. **Image tag** — preferably a sha-pinned tag (`v1.76.0@sha256:...`)
5. **Port** — application HTTP port
6. **Dependencies** — Flux Kustomizations this depends on (cross-namespace
   deps must include `namespace:`)
7. **Has secrets** — whether to scaffold an ExternalSecret

### Step 2: Create Directory

`kubernetes/apps/<namespace>/<app>/app/`

### Step 3: Generate Files

---

**`kubernetes/apps/<namespace>/<app>/ks.yaml`**
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
  targetNamespace: <namespace>
  path: ./kubernetes/apps/<namespace>/<app>/app
  interval: 1h
  timeout: 5m
  prune: true
  wait: false
  sourceRef:
    kind: GitRepository
    name: home-ops-kubernetes
    namespace: flux-system
  # dependsOn: include only if needed; cross-namespace deps require `namespace:`
```

---

**`kubernetes/apps/<namespace>/<app>/app/kustomization.yaml`**
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

The `components/repos/app-template/` Component supplies the shared
`OCIRepository/app-template`. Do not create a per-app `ocirepository.yaml`
for the app-template chart.

---

**`kubernetes/apps/<namespace>/<app>/app/helmrelease.yaml`**
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
  interval: 30m
  values:
    defaultPodOptions:
      securityContext:
        runAsGroup: 1000
        runAsNonRoot: true
        runAsUser: 1000
        # Add when the app needs to write to a PVC (uncomment + match GID):
        # fsGroup: 1000
        # fsGroupChangePolicy: OnRootMismatch
    controllers:
      <app>:
        annotations:
          reloader.stakater.com/auto: "true"
        containers:
          app:
            image:
              repository: <image-repo>
              tag: <image-tag>
            probes:
              liveness:
                enabled: true
                spec:
                  periodSeconds: 30
                  timeoutSeconds: 5
                  failureThreshold: 5
              readiness:
                enabled: true
                spec:
                  periodSeconds: 10
                  timeoutSeconds: 5
                  failureThreshold: 5
              startup:
                enabled: true
                spec:
                  failureThreshold: 30
                  periodSeconds: 10
            resources:
              requests:
                cpu: 10m
                memory: 128Mi
            securityContext:
              allowPrivilegeEscalation: false
              readOnlyRootFilesystem: true
              capabilities:
                drop: ["ALL"]
              seccompProfile:
                type: RuntimeDefault
    service:
      app:
        controller: <app>
        ports:
          http:
            port: <port>
```

Security defaults above match `.agents/instructions/helmrelease.security.md`.
If the app refuses to start under `readOnlyRootFilesystem: true`, the
fix is usually a `persistence: tmpfs: { type: emptyDir }` block mounted
at `/tmp` (and wherever else the app writes scratch). See that
instruction file for the workaround patterns.

Note on UID/GID: 1000 is the prevailing convention in this repo, but
match whatever the upstream image's non-root user is. Mismatches show
up as `Permission denied` on writes into the image's WORKDIR — see the
mealie-mcp postmortem for an example.

---

**`kubernetes/apps/<namespace>/<app>/app/externalsecret.yaml`** (optional)
```yaml
---
# yaml-language-server: $schema=https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/external-secrets.io/externalsecret_v1.json
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: <app>
spec:
  refreshInterval: 12h
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword-connect
  target:
    name: <app>-secret
    creationPolicy: Owner
  data:
    - secretKey: SECRET_KEY
      remoteRef:
        key: <app>
        property: secret_key
```

### Step 4: Wire Up Namespace Kustomization

Edit `kubernetes/apps/<namespace>/kustomization.yaml` and add the new
ks.yaml to `resources:` in alphabetical order.

### Step 5: Verify

`find kubernetes/apps/<namespace>/<app> -type f` — confirm files exist.
Check sorting/schemas with `.agents/instructions/*.instructions.md`.

## Notes

- The `app-template` chart version is pinned in
  `kubernetes/components/repos/app-template/ocirepository.yaml`. Bumps
  there cascade to every consumer — handle as its own PR.
- Sticking with the security defaults (non-root, read-only root FS,
  drop ALL caps) is repo convention; relax only with justification.
- Storage: see `.agents/instructions/storage-class.instructions.md` for
  picking between Rook/Ceph, Longhorn, and Garage.
