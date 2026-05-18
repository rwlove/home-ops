# RBAC Audit Findings

Snapshot date: 2026-05-18. Audit scope: every `ClusterRoleBinding`,
`ClusterRole`, and `RoleBinding` in the cluster, with extra attention
to anything bound to `cluster-admin` or carrying wildcard verbs /
resources.

This is an awareness document. **No RBAC was changed by this audit.**
Findings are sorted into tiers so a follow-up PR can pick off the
easiest, highest-value wins first.

## 1. Summary

| Metric | Count |
|---|---|
| Total `ClusterRoleBindings` | 154 |
| Total `ClusterRoles` | 200 |
| Total `RoleBindings` (all namespaces) | 121 |
| CRBs to `cluster-admin` | 5 |
| Non-system `ClusterRoles` with wildcard verbs or wildcard resources | 13 |
| `RoleBindings` referencing the built-in `admin`/`edit`/`view` ClusterRoles | 0 |

Headline reads: this cluster is in reasonable shape on RBAC. The
worst offender is a single helm-chart-shipped `longhorn-support-bundle`
binding to `cluster-admin` for a `ServiceAccount` that does not run
anything today. Everything else is either platform infrastructure
(Flux, Cilium, cert-manager, CNPG) or genuine operators whose grants
match their reconciler scope.

What this audit did **not** check: aggregated `view`-tier roles
(reflector, holmesgpt-extra-read, kubectl-mcp), `system:*`
ClusterRoles owned by kubeadm / kube-controller-manager, and the
default ServiceAccount surface area in each tenant namespace. Those
are worth a second pass if this one finds traction.

## 2. Tier 1 — Definitely over-permissive

These are bindings where `cluster-admin` (or equivalent unrestricted
power) is granted to a workload whose actual function does not
require it.

### 2.1 `longhorn-support-bundle` → `cluster-admin`

- **Workload**: `ServiceAccount/longhorn-system/longhorn-support-bundle`
- **Binding**: `ClusterRoleBinding/longhorn-support-bundle` → `ClusterRole/cluster-admin`
- **Runtime state**: No pod is using this SA right now (`kubectl get
  pods -n longhorn-system -l app=longhorn-support-bundle` returns
  nothing). The SA + binding exist because the Longhorn chart
  unconditionally ships them; the SA is only consumed when a support
  bundle is being collected, and the binding stays in place between
  collections.
- **Actual need**: A support-bundle collector needs broad **read**
  across the cluster (pods, services, events, CRs, logs) plus the
  ability to write its bundle output. `cluster-admin` is hugely
  beyond that — `view` plus targeted writes to its own namespace
  would do.
- **Proposed remediation**: File an upstream Longhorn issue
  requesting a scoped role for the support bundle SA; in the
  meantime, fork the binding to `ClusterRole/view` in the chart's
  values (or via a Flux post-render patch) so a leaked token cannot
  pivot to full cluster takeover. Confirm Longhorn's support-bundle
  collector still functions before merging.
- **Status (2026-05-18)**: Remediated via PR #11601 — Flux postRenderer
  patches the chart-shipped `ClusterRoleBinding/longhorn-support-bundle`
  from `cluster-admin` to `view`. Verify support-bundle collection still
  works the next time one is needed; if it fails on insufficient
  permissions, drop the patch and file the upstream issue.

This is the single most concerning finding because it is the cleanest
"unjustified" `cluster-admin` in the cluster.

## 3. Tier 2 — Probably over-permissive

These bindings are not `cluster-admin`, but the granted role carries
wildcards on resources or verbs that exceed the workload's apparent
need.

### 3.1 `reflector` → wildcard on `secrets` + `configmaps`

- **Workload**: `ServiceAccount/kube-system/reflector`
- **ClusterRole rule**: `apiGroups:[""], resources:[configmaps,secrets], verbs:["*"]`
- **Actual need**: Reflector reads source secrets/configmaps and
  mirrors them into other namespaces. It needs
  `get/list/watch/create/update/patch/delete` on those two resource
  types — which is basically `*`, but spelled out explicitly the
  list is auditable. `deletecollection` and any future verbs added
  by Kubernetes do not need to be auto-granted.
- **Status (2026-05-18)**: Remediated via the Tier 3 cleanup batch PR
  (`fix/rbac-tier-3-cleanup-batch`). Flux `postRenderers.kustomize`
  patches the chart-rendered `ClusterRole/reflector` `rules[0].verbs`
  from `["*"]` to the explicit
  `["get","list","watch","create","update","patch","delete"]`. The
  chart hardcodes the wildcard with no values knob; postRenderer is
  the only Flux-native option.

### 3.2 `openebs-localpv-provisioner` → wildcard `apiGroups`

- **Workload**: `ServiceAccount/storage/openebs-localpv-provisioner`
- **ClusterRole rule(s)**: Multiple rules with `apiGroups:["*"]` on
  `nodes`, `namespaces`, `pods`, `events`, `endpoints`,
  `resourcequotas`, `limitranges`, `storageclasses`,
  `persistentvolumeclaims`, `persistentvolumes`.
- **Actual need**: Those resources only exist in the core (`""`)
  API group. Using `apiGroups:["*"]` means a future CRD that
  happens to define a resource named `pods` (unusual but possible)
  would inherit these permissions.
- **Status (2026-05-18)**: **Accepted, not remediated** in the
  Tier 3 cleanup batch. The openebs chart's `localpv-provisioner`
  template hardcodes `apiGroups: ["*"]` across 5 separate rules with
  no values knob to scope them. A Flux postRenderer rewriting all 5
  rules' `apiGroups` would survive the current install but break the
  next chart upgrade if upstream adds a new rule or renames an
  existing one — the deferred-failure mode is worse than the
  steady-state risk for what is purely defensive against a future
  same-named-resource CRD collision. Same reasoning pattern as
  §3.3 longhorn-role.

### 3.3 `longhorn-role` → wildcard on `clusterrolebindings` + `clusterroles`

- **Workload**: `ServiceAccount/longhorn-system/longhorn-service-account`
- **Rule**: `apiGroups:["rbac.authorization.k8s.io"], resources:[clusterrolebindings,clusterroles], verbs:["*"]`
- **Actual need**: Longhorn creates per-driver RBAC at install
  time, but at steady state it reconciles its own existing
  bindings rather than minting fresh cluster-scoped RBAC. A
  compromised Longhorn manager pod can grant itself `cluster-admin`
  via this rule, which makes Longhorn an implicit
  privilege-escalation path equivalent to Tier 1.
- **Status (2026-05-18)**: **Accepted, documented, not remediated**.
  The longhorn chart's `longhorn-role` template hardcodes this rule
  in `clusterrole.yaml` with no values knob to scope it, and Longhorn
  uses these permissions during chart upgrades to reconcile its own
  per-driver `ClusterRole`/`ClusterRoleBinding` set (longhorn-manager,
  longhorn-ui-service-account, CSI components). Patching the rule
  via Flux postRenderer to add `resourceNames` would survive the
  current install but break the next chart upgrade if Longhorn adds a
  new role or renames an existing one — a deferred failure mode that
  is worse than the steady-state risk.
- **Blast radius if compromised**: Total cluster takeover. Any pod
  running with `longhorn-service-account` (longhorn-manager DaemonSet
  on every node; longhorn-driver-deployer; longhorn-csi components)
  can `kubectl create clusterrolebinding self-admin --clusterrole=
  cluster-admin --serviceaccount=longhorn-system:longhorn-service-
  account` and have full cluster control on the next reconcile. This
  is on par with Tier 1 (`longhorn-support-bundle` → `cluster-admin`,
  remediated separately) — except the support-bundle SA had no pod
  consuming it, whereas longhorn-service-account is mounted into
  ~10 pods per node continuously.
- **Compensating controls**:
  - Longhorn-system namespace network policy restricts egress (see
    `kubernetes/components/network-policy/`); a compromised longhorn-
    manager cannot exfiltrate to arbitrary endpoints without first
    pivoting to a host with looser egress.
  - Longhorn images are pinned by tag (v1.11.0-hotfix-1) and pulled
    via the in-cluster ZOT registry cache, reducing supply-chain
    surface vs pulling tag-floating from docker.io directly.
  - Backup target (`nfs://beast:/mnt/mass_storage/longhorn-backups`)
    is read+write from longhorn-manager pods, so a compromise that
    encrypts or deletes the backup target would defeat the
    cluster-loss-survivability tier. **This is the highest-impact
    blast-radius dimension** — see drill proposal below.
- **Backup-restore drill proposal**:
  - Simulate a compromised longhorn-manager by manually creating an
    arbitrary `ClusterRoleBinding` and confirming the cluster
    detects it (via the existing kube-prometheus-stack rules or a
    new PrometheusRule that alerts on non-chart-managed CRBs
    referencing `cluster-admin`).
  - Validate that the most recent Longhorn backup is restorable from
    the NFS target after rotating the longhorn-system kubeconfig
    (i.e. assume the backup target was preserved but the cluster
    state is gone). Recovery procedure in
    `docs/src/cluster_rebuild.md`; verify the Longhorn step
    end-to-end against a representative volume.
  - Drill cadence: annual. The Longhorn structural permissions are
    not going to change without a major chart redesign; the drill
    validates we can recover regardless.

### 3.4 `goldilocks-controller` → wildcard `resources` under `apps`

- **Workload**: `ServiceAccount/observability/goldilocks-controller`
- **Rule**: `apiGroups:["apps"], resources:["*"], verbs:["get","list","watch"]`
- **Actual need**: Goldilocks reads `deployments`,
  `statefulsets`, `daemonsets` to make VPA recommendations. The
  wildcard would also cover `controllerrevisions` and any future
  `apps/*` resource.
- **Status (2026-05-18)**: **Partially remediated** in the Tier 3
  cleanup batch. The `apps/*` wildcard itself is hardcoded in the
  chart with no scoping knob, so the read-only `apps/*` rule is
  **accepted** (read-only blast radius is small).
  The adjacent `argoproj.io/rollouts` rule — added unconditionally to
  both `goldilocks-controller` and `goldilocks-dashboard` ClusterRoles
  — was dropped by setting `controller.rbac.enableArgoproj: false`
  and `dashboard.rbac.enableArgoproj: false`. We don't run Argo
  Rollouts in this cluster (no `argoproj.io/v1alpha1/Rollout` CRD
  installed), so the rule had nothing to read; removing it tightens
  cluster-wide read by one apiGroup at zero functional cost.

## 4. Tier 3 — Worth auditing

These are not obviously wrong, but they warrant a deeper look the
next time the relevant chart is touched.

| Workload | Role / binding | Concern | Status (2026-05-18) |
|---|---|---|---|
| `renovate-operator` (renovate ns) | `ClusterRole/renovate-operator` grants `create/get/list/watch/update/delete` on **all** `secrets` cluster-wide | Renovate mints credential secrets per-job; needs to read its source secrets. Wildcard cluster-wide write feels broader than the workload calls for — verify whether it can be namespace-scoped to `renovate` only. | **Remediated** PR #11602 — `rbac.ownNamespaceOnly: true`; operator now uses Role + RoleBinding scoped to `renovate` ns. |
| `k8tz` (kube-system) | `ClusterRole/k8tz-role` grants `*` on `configmaps` + `secrets` cluster-wide | Mutating webhook for TZ injection. Needs read on a small set of CMs/Secrets; cluster-wide `*` is over-broad. | **Audit doc was wrong — corrected and accepted** in Tier 3 cleanup batch. The live rule grants only `get/list/watch` on `secrets` (not `*`, and not on `configmaps` at all). The rule is conditional on `webhook.certManager.enabled: true` so k8tz can read its own cert-manager-issued webhook TLS Secret (`k8tz-webhook-ca` in `kube-system`). The chart hardcodes the cluster-wide scope with no `resourceNames` knob. Read-only on a narrow predictable name — accept. |
| `netdata` (observability) | Reads `secrets` cluster-wide | Used to populate dashboards; revisit whether it actually consumes Secret values or just enumerates names. | **Accepted** in Tier 3 cleanup batch. Live rule is `get/list/watch` (not write) on `secrets` + `configmaps` cluster-wide. Used by netdata's k8s-state collector for service discovery (pod env, configmap volume references) to enrich dashboards. Chart provides only a binary `rbac.create: true/false` knob — no scoping option. We already run `parent`-only (`child.enabled: false`, `k8sState.enabled: false`); read-only blast radius on a parent-only deployment is acceptable. |
| `actions-runner-set-home-ops-gha-rs-kube-mode` (Role, actions-runner-system ns) | `pods/exec`, `secrets create/delete` within the ns | Standard GHA kube-mode pattern; in-namespace scope contains the blast radius. Listed here so it is not surprising. | **Accepted, no change** in Tier 3 cleanup batch. The actual deployed Role is named `arc-runner-set-home-ops-gha-rs-kube-mode` and is already correctly a namespace-scoped `Role` (not a `ClusterRole`). It grants `pods`, `pods/exec`, `pods/log`, `jobs`, `secrets` within the `actions-runner-system` namespace only — the standard GHA kube-mode pattern, blast radius contained. The companion `arc-runner-set-home-ops-gha-rs-manager` Role grants the controller `roles`/`rolebindings`/`secrets`/`serviceaccounts` in the same namespace so it can mint per-job ephemeral RBAC. Both are appropriate; the cluster-wide secrets gap that Tier 2 fixed was a separate finding. |
| `holmesgpt-view` (observability) | Built-in `view` cluster-wide | View tier reads almost everything except secrets. HolmesGPT is an LLM-fed pipeline; if the model context ever flows back to a less-trusted channel, this matters. | **Accepted** in Tier 3 cleanup batch. The built-in `view` ClusterRole excludes Secrets by design. HolmesGPT's value proposition is correlated triage across the whole cluster (pods, events, nodes, services, CRs across every namespace) — narrowing scope would defeat the tool. The `holmesgpt-extra-read` companion ClusterRole (declared in this repo at `kubernetes/apps/observability/holmesgpt/app/rbac.yaml`) is already explicit (no wildcards) and read-only. Mitigation: HolmesGPT runs against a local Ollama backend (`OLLAMA_API_BASE: http://ollama.ai.svc.cluster.local`) — no model context flows offsite. |
| `kubectl-mcp-kubectl-mcp-read-all` (mcp-system) | Custom read-all cluster-wide | Already restricted to `get/list/watch` and excludes secrets; flagged only to confirm no future PRs widen it. | **Accepted, no change** in Tier 3 cleanup batch. ClusterRole is declared inline in this repo at `kubernetes/apps/mcp-system/kubectl-mcp/app/helmrelease.yaml` under `values.rbac.roles.kubectl-mcp-read-all`. Rule set is explicit (no wildcards), read-only, and excludes `secrets`. Companion `kubectl-mcp-write-restricted` is a namespace-scoped `Role` granting only `pods delete`, `pods/exec create`, `jobs delete`, `deployments patch/update` in `mcp-system`. Both are appropriately scoped for the MCP server's introspection workload. |

## 5. Whitelist — Bindings that ARE justified

So these do not get re-flagged in subsequent audits.

### 5.1 `cluster-admin` bindings

| Subject | Justification |
|---|---|
| `Group/system:masters` | Built-in. Required for the bootstrap kubeconfig. Untouchable. |
| `Group/kubeadm:cluster-admins` | Built-in (kubeadm). Required for the cluster-admin group on the cluster. |
| `flux-system/kustomize-controller` + `flux-system/helm-controller` (via `cluster-reconciler-flux-system`) | Flux GitOps reconcilers must be able to apply arbitrary cluster resources by design. This is the whole-cluster GitOps model. |
| `flux-system/flux-operator` | Same — the operator manages Flux itself across all namespaces. |
| `longhorn-system/longhorn-support-bundle` | **Not** whitelisted. See §2.1. |

### 5.2 Broad-but-justified `ClusterRoles`

- `cilium-operator`, `cilium` — eBPF CNI needs cluster-wide visibility into pods/nodes/services to install datapath state.
- `cert-manager` family — issuance machinery must reach CSRs, secrets, ingresses cluster-wide.
- `cloudnative-pg` — operates per-tenant `Cluster` CRs in any namespace.
- `cloudnative-pg`'s sibling `plugin-barman-cloud` — same scope.
- `istiod-clusterrole-istio-system` — service-mesh control plane requires cluster-wide visibility of network resources.
- `external-secrets-controller`, `external-secrets-cert-controller` — by design reads/writes Secrets in every tenant namespace.
- `kube-prometheus-stack-operator`, `kube-prometheus-stack-prometheus`, `kube-state-metrics`, `vector-agent`, `loki`, `grafana-clusterrole` (read-only on configmaps/secrets) — observability stack needs cluster-wide read.
- `crd-controller-flux-system` — wildcard on every Flux apiGroup, but bound exclusively to Flux's own controllers; intrinsically justified.
- `rook-ceph-system`, `rook-ceph-global`, `rook-ceph-mgr-cluster`, `rook-ceph-osd` — storage operator scope.
- All `ceph-csi-*` plugins — CSI drivers must enumerate volumes/snapshots cluster-wide.
- `node-feature-discovery`, `nvidia-*`, `gpu-operator`, `inteldeviceplugins-*` — device plugins need cluster-wide node visibility.
- `multus`, `coredns`, `kube-vip`, `metrics-server`, `descheduler`, `node-problem-detector`, `kubelet-csr-approver` — networking/node-level platform components.
- `coroot-cluster-agent`, `kube-ops-view`, `goldilocks-dashboard` (read-only on `apps/*`), `silence-operator` — observability with cluster-wide read needs.
- `vpa-*`, `goldilocks-controller` (with the wildcard-resources caveat in §3.4) — VPA needs cluster-wide workload visibility.
- `actions-runner-controller` (cluster-scoped CRs in its own apiGroup) — controller manages its own CRDs cluster-wide; the in-namespace workflow-pod Role is the more interesting one (Tier 3).
- `mcp-gateway-controller` — manages `mcp.kuadrant.io` CRs and Gateway/HTTPRoute resources cluster-wide; scope matches reconciler.
- `glance` — dashboard widget; rule set is narrow (list-only on a small set of cluster-scoped resources).

## 6. Notes for the next pass

- Aggregated `system:*` roles owned by kube-controller-manager were
  not audited; they are upstream and changing them would diverge
  from kubeadm.
- Service-account-scope hygiene per workload (i.e. is each
  HelmRelease running under a dedicated SA vs the namespace
  `default`?) was not checked; would be a good follow-up.
- No OPA / Kyverno / ValidatingAdmissionPolicy rules were
  evaluated — the standing rule in the audit prompt is to not
  reach for those yet.
