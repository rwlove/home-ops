@.agents/instructions/configmap.resources.instructions.md
@.agents/instructions/flux.sorting.instructions.md
@.agents/instructions/helmfile.sorting.instructions.md
@.agents/instructions/helmrelease.security.md
@.agents/instructions/kustomize.config.sorting.instructions.md
@.agents/instructions/persona.md
@.agents/instructions/schema.correction.md
@.agents/instructions/storage-class.instructions.md

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

See `.agents/skills/pr-review.md`.
