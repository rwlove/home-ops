# ConfigMap source files live in `app/resources/`

When a HelmRelease needs application config baked into a ConfigMap via
`configMapGenerator` (or `secretGenerator`), the source files MUST live
under an `app/resources/` subdirectory and MUST be named exactly as
they appear inside the container.

## Rules

- Source file path: `kubernetes/apps/<group>/<app>/app/resources/<filename>`.
- `<filename>` is the **in-container basename** (e.g. `config.yaml`,
  `settings.yml`, `application.yml`, `nut.conf`). Do not rename
  between disk and container.
- The `configMapGenerator` `files:` entry uses the explicit
  `<filename>=./resources/<filename>` form. Do **not** use the bare
  `./resources/<filename>` form — the explicit form keeps the
  in-container key greppable from the kustomization.
- Mount the resulting ConfigMap in the HelmRelease's `persistence.*`
  block with `subPath: <filename>` matching the key.

## Example

```yaml
# kubernetes/apps/<group>/<app>/app/kustomization.yaml
configMapGenerator:
  - name: <app>-configmap
    files:
      - config.yaml=./resources/config.yaml
generatorOptions:
  disableNameSuffixHash: true
```

```yaml
# kubernetes/apps/<group>/<app>/app/helmrelease.yaml
spec:
  values:
    persistence:
      config-file:
        type: configMap
        name: <app>-configmap
        advancedMounts:
          <app>:
            app:
              - path: /config/config.yaml
                subPath: config.yaml
                readOnly: true
```

## Why

- One filename, three places (disk, ConfigMap key, in-container path) —
  trivially traceable.
- Dovetails with `schema.correction.md`'s rule that schemas are NOT
  applied to anything under a `resources/` dir — application-config
  payloads aren't k8s manifests.
- A `kustomize build` rendering the same ConfigMap data regardless of
  whether the source is under `config/`, at the kustomization root, or
  under `resources/` makes the convention purely organizational —
  enforce it for grep-ability and onboarding clarity.

## When this does NOT apply

- **Helm chart values** fed to a HelmRelease via `valuesFrom: kind:
  ConfigMap`. These are consumed by Flux at render time and never
  mounted into a container; the "matches in-container path" rule has
  no meaning. Existing repo convention: `./values.yaml` next to the
  kustomization (or `./helm/values.yaml` for some platform charts).
- **Grafana dashboards** generated as ConfigMaps with the
  `grafana_dashboard: "true"` label. The established pattern for those
  is a sibling `dashboard/kustomization.yaml` with `dashboard.json`
  next to it; do not move dashboards under `resources/`.
- **Kubernetes manifests** (HelmRelease, ExternalSecret, PVC, etc.) —
  these belong next to `kustomization.yaml`, not under `resources/`.
