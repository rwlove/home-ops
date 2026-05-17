# bootstrap/

One-shot resources applied from the laptop before Flux can take over. Runs once per cluster bootstrap; nothing here is reconciled or re-applied later.

The full cluster-bring-up workflow lives in [`docs/src/init_teardown.md`](../docs/src/init_teardown.md) and [`docs/src/cluster_rebuild.md`](../docs/src/cluster_rebuild.md). This README documents what each file in `bootstrap/` does and how they fit together.

## Layout

```text
bootstrap/
├── helmfile.d/
│   ├── 00-crds.yaml       # CRDs that must exist before any HelmRelease lands
│   ├── 01-apps.yaml       # Bootstrap apps: Cilium, CoreDNS, cert-manager,
│   │                      # external-secrets + 1Password Connect, Flux operator + instance
│   └── templates/         # Shared helmfile partials
├── mod.just               # `just` recipes: render resources, helm bootstrap
└── resources.yaml.j2      # 1Password-templated Secrets applied before Flux comes up
```

## Files

### `resources.yaml.j2`

Jinja-style template rendered by `op inject` (1Password CLI). Produces the two pre-Flux Secrets that everything else depends on:

- `external-secrets/onepassword-connect-secret` — 1Password Connect credentials + token. Without this, no `ExternalSecret` in the cluster can resolve.
- `flux-system/cluster-secrets` — cluster-wide variables (`SECRET_DOMAIN`, `EMAIL`, `NFS_HOST_*`) consumed by Flux Kustomization `postBuild.substituteFrom`.

`op://` URIs reference items in the `kubernetes` 1Password vault. The render command (`just -f bootstrap/mod.just resources`) requires `op` to be signed in.

### `helmfile.d/00-crds.yaml`

CRDs that must exist *before* any HelmRelease can be parsed. Applied first because helmfile installing an operator that owns a CRD doesn't help an `ExternalSecret` manifest already in Git fail validation if the CRD isn't registered yet. Includes: external-secrets, cert-manager, gateway-api, kustomize-mutating-webhook CRDs.

### `helmfile.d/01-apps.yaml`

The minimal set of cluster apps that need to run before Flux can take over. Order matters:

1. Cilium — the cluster has no networking without it; Pods can't even talk to apiserver
2. CoreDNS — Pods can't resolve service names without it
3. cert-manager + the Cloudflare ClusterIssuer — required by anything with a hostname
4. external-secrets + 1Password Connect — required by anything with a Secret
5. Flux operator + Flux instance — from this point on, everything else flows from Git

After `01-apps.yaml` finishes, Flux pulls the `home-ops-kubernetes` GitRepository and reconciles the rest of `kubernetes/`.

### `mod.just`

Recipes invoked from the repo-root `justfile`:

| Recipe | What it does |
|---|---|
| `just bootstrap resources` | Render `resources.yaml.j2` with `op inject`, then `kubectl apply --server-side` |
| `just bootstrap helm-crds` | `helmfile sync --file bootstrap/helmfile.d/00-crds.yaml` |
| `just bootstrap helm-apps` | `helmfile sync --file bootstrap/helmfile.d/01-apps.yaml` |

The end-to-end `./init/initialize-cluster.sh` script runs these in the right order; you rarely invoke them individually.

## Prerequisites

Operator-side (laptop):

- `kubectl`
- `helmfile`
- `helm` (helmfile shells out to it)
- `op` (1Password CLI, signed in to a session that can access the `kubernetes` vault)
- `just`

Cluster-side (`master1`):

- A kubeadm-joined control plane reachable via the VIP at `192.168.6.1`
- A kubeconfig the laptop can read (typically pulled by `init/initialize-cluster.sh`)

## When bootstrap re-runs

Almost never. Bootstrap is one-shot per cluster lifetime. After `01-apps.yaml` lands, Flux owns Cilium / CoreDNS / cert-manager / external-secrets / 1Password Connect — bootstrap's job ends there.

The exceptions are:

- **Cluster rebuild** ([`cluster_rebuild.md`](../docs/src/cluster_rebuild.md)): teardown + bootstrap + Flux take-over again.
- **Adding a new cluster-secret variable** (e.g. a new `NFS_HOST_X`): edit `resources.yaml.j2`, run `just bootstrap resources` to update the `cluster-secrets` Secret. The change does not flow through Git because the rendered output never lands in Git.

## Related

- [`init/`](../init/) — the shell scripts that orchestrate bootstrap (create-cluster, initialize-cluster, destroy-cluster, kube-vip).
- [`docs/src/init_teardown.md`](../docs/src/init_teardown.md) — minimal bring-up procedure.
- [`docs/src/cluster_rebuild.md`](../docs/src/cluster_rebuild.md) — full bootstrap + recovery walkthrough.
