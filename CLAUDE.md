@.agents/instructions/sorting.instructions.md

# Home Operations - AI Assistant Guide

This is a **Home Kubernetes cluster monorepo** managed with GitOps (Flux, Renovate, GitHub Actions).

## Repository Structure

```
home-ops/
├── kubernetes/          # Kubernetes configurations (Flux-managed)
│   ├── apps/            # Application configs
│   ├── components/      # Reusable k8s components
│   └── flux/            # Flux cluster definitions
├── bootstrap/           # Bootstrap templates (helmfile.d, templates)
├── docs/                # mdBook documentation
├── init/		 # Cluster initialization scripts
└── tools/		 # Helper scripts
```

## Cluster Architecture

#- **main** - 3x MS-01 + 1x Bosgame M5 (i9-13900H x3, Ryzen AI Max+ 395 x1, 128GB RAM), hyper-converged storage
#- **utility** - 1x Bosgame P1 (Ryzen 7 5700U), low-power services
#- **test** - 1x Beelink Mini-S (Celeron N5095), testing

## Key Technologies

| Category   | Tool                         | Purpose                           |
|------------|------------------------------|-----------------------------------|
| GitOps     | Flux                         | Deploys configs from Git to k8s   |
| CI         | Renovate + GitHub Actions    | Dependency updates, automation    |
| Networking | cilium (eBPF)                | CNI, BGP, service mesh            |
| Ingress    | Envoy Gateway                | L7 proxy, ingress controller      |
| DNS        | external-dns                 | Syncs ingress to Cloudflare/bind  |
| Secrets    | external-secrets + 1Password | Secret management                 |
| Storage    | Rook/Ceph, Longhorn, Garage  | Distributed storage + backups     |
| Images     | ZOT                          | Local container cache             |

## GitOps Flow

```
Git push → Flux source sync → Kustomization → HelmRelease → k8s resources
```

Flux recursively searches `kubernetes/apps/` for `kustomization.yaml` files. Each must define a namespace and Flux kustomization (`ks.yaml`).

## Conventions

- Component READMEs stay with components (e.g., `kubernetes/apps/base/cilium/README.md`)
- Secrets stored in 1Password, referenced via `external-secrets`
- Apps use `HelmRelease` via Flux, rarely raw manifests
- Clusters are mostly identical except for app selections and sizing

## Common Operations

- **Add app**: Create in `kubernetes/apps/` with kustomization + HelmRelease
- **Update app**: Merge renovate PR or manually edit and push
- **Troubleshoot**: Check `flux get all -n <namespace>`, `kubectl get events --sort-by=.lastTimestamp`
- **Scripts**: `tools/` contains operational scripts (get-ceph-password.sh, run-on-all-nodes.sh, etc.)

## Documentation

- Main docs: `/docs/src/` (mdBook)
- Component docs: README files co-located with components
#- Personal notes: `/docs/src/notes/`

## Adding Documentation

When adding architecture or operational docs, consider:
1. Put user-facing docs in `/docs/src/`
2. Keep component-specific docs with the component
3. Personal notes go in `/docs/src/notes/`

## PR Review Standards

When reviewing Renovate PRs, enforce these criteria:

### HelmRelease Requirements
- All applications MUST use `HelmRelease` via Flux, not raw manifests
- Must include `spec.chart.spec.version` for pinned chart versions outside of `app-template`
- Must include `spec.interval` for reconciliation frequency
- Resource limits (CPU/memory) SHOULD be specified for production workloads, but this is not a hard requirement
- `valuesFrom` should reference ConfigMaps/Secrets, not inline values

### Secret Management Rules
- **NEVER** commit plain-text secrets or credentials in Git
- All secrets MUST use `external-secrets` with 1Password backend
- If a PR introduces a new secret, verify it's external-secrets backed

### Image & Digest Policy
- Prefer `@sha256:` digests over version tags for reproducibility
- For tag-only updates, verify OCI metadata (revision/source/created)
- If revision changes between digests, ensure it's intentional
- Reject updates from untrusted registries (must be allowlisted)
- Preferred registries: GHCR.io, registry.k8s.io, Docker Hub (fallback)
- Avoid Docker Hub for critical infrastructure components

### Cluster-Specific Policies
- Strict validation - all standards must be met

### Breaking Change Detection
Always `request_changes` if:
- API version changes (e.g., `apiVersion: apps/v1beta1` → `apps/v1`)
- Deprecated field usage introduced
- Major version bumps without justification
- CRD changes or custom resource modifications
- Network policy or security context relaxations

### Required Evidence for Approval
Before approving, verify:
1. Release notes/changelog mention the upgrade
2. GitHub compare shows expected changes
3. Version aligns with what Renovate reported
4. No breaking changes identified in release notes
5. Security advisories don't apply to this version

_Flux automatically reconciles changes once the PR is merged._
