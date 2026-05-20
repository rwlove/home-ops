@.agents/instructions/configmap.resources.instructions.md
@.agents/instructions/data-classification.md
@.agents/instructions/flux.sorting.instructions.md
@.agents/instructions/gpu-routing.md
@.agents/instructions/helmfile.sorting.instructions.md
@.agents/instructions/helmrelease.security.md
@.agents/instructions/kustomize.config.sorting.instructions.md
@.agents/instructions/persona.md
@.agents/instructions/schema.correction.md
@.agents/instructions/storage-class.instructions.md
@.agents/instructions/workarounds.md

# Home Operations - AI Assistant Guide

This is a **Home Kubernetes cluster monorepo** managed with GitOps (Flux, Renovate, GitHub Actions).

## Repository Structure

```text
home-ops/
├── kubernetes/          # Kubernetes configurations (Flux-managed)
│   ├── apps/            # Application configs
│   ├── components/      # Reusable k8s components
│   └── flux/            # Flux cluster definitions
├── bootstrap/           # Bootstrap templates (helmfile.d, templates)
├── docs/                # mdBook documentation
├── init/                # Cluster initialization scripts
└── tools/                # Helper scripts
```

## Key Technologies

| Category      | Tool                         | Purpose                                          |
|---------------|------------------------------|--------------------------------------------------|
| GitOps        | Flux                         | Deploys configs from Git to k8s                  |
| CI            | Renovate + GitHub Actions    | Dependency updates, automation                   |
| Networking    | Cilium (eBPF)                | CNI, BGP peering, LoadBalancer pool              |
| Ingress       | Envoy Gateway                | L7 gateway / HTTPRoute                           |
| Service mesh  | Istio                        | mTLS + traffic mgmt for mcp-system               |
| DNS           | external-dns                 | Cloudflare + bind9 split-horizon                 |
| Tunnel        | cloudflared                  | Public ingress without exposing home WAN         |
| AuthN/Z       | Authelia + oauth2-proxy      | OIDC SSO; ~24 oauth2-proxy instances             |
| Secrets       | external-secrets + 1Password | Secret management (109 ExternalSecrets)          |
| Storage       | Rook/Ceph, Longhorn, Garage  | Tiered durable storage; see `storage-class` instr |
| Databases     | CloudNative-PG               | 24+ Postgres clusters with Garage Barman backup  |
| Observability | kube-prometheus-stack, Loki, Grafana, HolmesGPT | Metrics, logs, AI alert triage |
| Images        | ZOT                          | Pull-through registry cache                      |

## GitOps Flow

```text
Git push → Flux source sync → Kustomization → HelmRelease → k8s resources
```

Flux recursively searches `kubernetes/apps/` for `kustomization.yaml` files. Each must define a namespace and Flux kustomization (`ks.yaml`).

## Conventions

- Component READMEs stay with components (e.g., `kubernetes/components/network-policy/baseline/README.md`)
- Secrets stored in 1Password, referenced via `external-secrets`
- Apps use `HelmRelease` via Flux, rarely raw manifests
- Clusters are mostly identical except for app selections and sizing

### Flux suspend / disable workflow

Look out for the `disable-<app>` / `Revert "disable-<app>"` commit
pattern in `git log`. The user manually pauses an app's reconciliation
when they need to break the GitOps loop temporarily — typically when a
release is in flight and they don't want Flux clobbering their hand
edits, or to take an app offline for maintenance.

**Do not** revert these on the user's behalf, "fix" them, or unsuspend
a Flux Kustomization without explicit instruction. If a `Suspended:
True` status shows up unexpectedly, ask before touching it.

## Common Operations

- **Add app**: Create in `kubernetes/apps/` with kustomization + HelmRelease
- **Update app**: Merge renovate PR or manually edit and push
- **Troubleshoot**: Check `flux get all -n <namespace>`, `kubectl get events --sort-by=.lastTimestamp`
- **Scripts**: `tools/` contains operational scripts (get-ceph-password.sh, run-on-all-nodes.sh, etc.)

## Documentation

- Main docs: `/docs/src/` (mdBook, rendered at <https://rwlove.github.io/home-ops/>)
- Repo-wide README: `/README.md` (the home-ops landing page)
- Component docs: README files co-located with components
- Agent-loaded conventions: `/.agents/instructions/` (auto-imported via this CLAUDE.md)
- Agent skills: `/.agents/skills/` (invoked on demand)

## Adding Documentation

When adding architecture or operational docs, consider:

1. **Operator runbooks** → `/docs/src/` (mdBook chapters listed in `SUMMARY.md`)
2. **Component-specific** → README next to the component (e.g. `kubernetes/components/network-policy/baseline/README.md`)
3. **Conventions every AI session should auto-load** → `/.agents/instructions/` plus an `@`-import line in this file
4. **One-shot agent workflows** → `/.agents/skills/` (not auto-loaded; invoked explicitly)

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
