# Correct schemas

Whenever requested to fix or correct schemas, follow these instructions:

**Default rules**

- only apply schemas to yaml files
- do not apply schemas to files in any directory named `resources`
- if you cannot find the right schema to apply to a file then append `# TODO: apply schema`
- schemas should always follow `---`
- there is always a schema defined on the second line of a yaml file
- remove incorrect schemas and replace them with the correct schema comment
- a `kind: Component` (apiVersion `kustomize.config.k8s.io/v1alpha1`)
  uses the same kustomization schema as `kind: Kustomization`

## apiVersion + kind to schema mappings

### Flux

| apiVersion | kind | schema |
|------------|------|--------|
| `kustomize.toolkit.fluxcd.io/v1` | `Kustomization` | `https://raw.githubusercontent.com/fluxcd-community/flux2-schemas/main/kustomization-kustomize-v1.json` |
| `helm.toolkit.fluxcd.io/v2` | `HelmRelease` | `https://raw.githubusercontent.com/fluxcd-community/flux2-schemas/main/helmrelease-helm-v2.json` |
| `source.toolkit.fluxcd.io/v1` | `OCIRepository` | `https://raw.githubusercontent.com/fluxcd-community/flux2-schemas/main/ocirepository-source-v1.json` |
| `source.toolkit.fluxcd.io/v1` | `HelmRepository` | `https://raw.githubusercontent.com/fluxcd-community/flux2-schemas/main/helmrepository-source-v1.json` |
| `source.toolkit.fluxcd.io/v1` | `GitRepository` | `https://raw.githubusercontent.com/fluxcd-community/flux2-schemas/main/gitrepository-source-v1.json` |
| `notification.toolkit.fluxcd.io/v1beta3` | `Alert` | `https://raw.githubusercontent.com/fluxcd-community/flux2-schemas/main/alert-notification-v1beta3.json` |
| `notification.toolkit.fluxcd.io/v1beta3` | `Provider` | `https://raw.githubusercontent.com/fluxcd-community/flux2-schemas/main/provider-notification-v1beta3.json` |
| `notification.toolkit.fluxcd.io/v1` | `Receiver` | `https://raw.githubusercontent.com/fluxcd-community/flux2-schemas/main/receiver-notification-v1.json` |

### Kustomize

| apiVersion | kind | schema |
|------------|------|--------|
| `kustomize.config.k8s.io/v1beta1` | `Kustomization` | `https://json.schemastore.org/kustomization` |
| `kustomize.config.k8s.io/v1alpha1` | `Component` | `https://json.schemastore.org/kustomization` |

### Core Kubernetes

| apiVersion | kind | schema |
|------------|------|--------|
| `v1` | `PersistentVolumeClaim` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/persistentvolumeclaim-v1.json` |
| `v1` | `PersistentVolume` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/persistentvolume-v1.json` |
| `v1` | `Namespace` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/namespace-v1.json` |
| `v1` | `ConfigMap` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/configmap-v1.json` |
| `v1` | `Secret` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/secret-v1.json` |
| `v1` | `Service` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/service-v1.json` |
| `v1` | `ServiceAccount` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/serviceaccount-v1.json` |
| `v1` | `Pod` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/pod-v1.json` |
| `apps/v1` | `Deployment` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/deployment-apps-v1.json` |
| `apps/v1` | `StatefulSet` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/statefulset-apps-v1.json` |
| `apps/v1` | `DaemonSet` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/daemonset-apps-v1.json` |
| `apps/v1` | `ReplicaSet` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/replicaset-apps-v1.json` |
| `batch/v1` | `CronJob` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/cronjob-batch-v1.json` |
| `batch/v1` | `Job` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/job-batch-v1.json` |
| `scheduling.k8s.io/v1` | `PriorityClass` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/priorityclass-scheduling-v1.json` |
| `networking.k8s.io/v1` | `NetworkPolicy` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/networkpolicy-networking-v1.json` |

### RBAC

| apiVersion | kind | schema |
|------------|------|--------|
| `rbac.authorization.k8s.io/v1` | `ClusterRole` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/clusterrole-rbac-v1.json` |
| `rbac.authorization.k8s.io/v1` | `ClusterRoleBinding` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/clusterrolebinding-rbac-v1.json` |
| `rbac.authorization.k8s.io/v1` | `Role` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/role-rbac-v1.json` |
| `rbac.authorization.k8s.io/v1` | `RoleBinding` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/rolebinding-rbac-v1.json` |

### External Secrets

| apiVersion | kind | schema |
|------------|------|--------|
| `external-secrets.io/v1` | `ExternalSecret` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/external-secrets.io/externalsecret_v1.json` |
| `external-secrets.io/v1` | `ClusterSecretStore` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/external-secrets.io/clustersecretstore_v1.json` |
| `generators.external-secrets.io/v1alpha1` | `GithubAccessToken` | (no upstream JSON schema yet — append `# TODO: apply schema`) |

### Gateway API + Envoy Gateway

| apiVersion | kind | schema |
|------------|------|--------|
| `gateway.networking.k8s.io/v1` | `GatewayClass` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/gateway.networking.k8s.io/gatewayclass_v1.json` |
| `gateway.networking.k8s.io/v1` | `Gateway` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/gateway.networking.k8s.io/gateway_v1.json` |
| `gateway.networking.k8s.io/v1` | `HTTPRoute` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/gateway.networking.k8s.io/httproute_v1.json` |
| `gateway.envoyproxy.io/v1alpha1` | `EnvoyProxy` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/gateway.envoyproxy.io/envoyproxy_v1alpha1.json` |
| `gateway.envoyproxy.io/v1alpha1` | `EnvoyExtensionPolicy` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/gateway.envoyproxy.io/envoyextensionpolicy_v1alpha1.json` |
| `gateway.envoyproxy.io/v1alpha1` | `BackendTrafficPolicy` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/gateway.envoyproxy.io/backendtrafficpolicy_v1alpha1.json` |
| `gateway.envoyproxy.io/v1alpha1` | `ClientTrafficPolicy` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/gateway.envoyproxy.io/clienttrafficpolicy_v1alpha1.json` |
| `gateway.envoyproxy.io/v1alpha1` | `SecurityPolicy` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/gateway.envoyproxy.io/securitypolicy_v1alpha1.json` |

### Observability

| apiVersion | kind | schema |
|------------|------|--------|
| `monitoring.coreos.com/v1` | `ServiceMonitor` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/monitoring.coreos.com/servicemonitor_v1.json` |
| `monitoring.coreos.com/v1` | `PodMonitor` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/monitoring.coreos.com/podmonitor_v1.json` |
| `monitoring.coreos.com/v1` | `Probe` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/monitoring.coreos.com/probe_v1.json` |
| `monitoring.coreos.com/v1` | `PrometheusRule` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/monitoring.coreos.com/prometheusrule_v1.json` |
| `monitoring.coreos.com/v1alpha1` | `AlertmanagerConfig` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/monitoring.coreos.com/alertmanagerconfig_v1alpha1.json` |
| `monitoring.coreos.com/v1alpha1` | `ScrapeConfig` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/monitoring.coreos.com/scrapeconfig_v1alpha1.json` |
| `observability.giantswarm.io/v1alpha2` | `Silence` | (no upstream JSON schema yet — append `# TODO: apply schema`) |

### Storage / Database

| apiVersion | kind | schema |
|------------|------|--------|
| `postgresql.cnpg.io/v1` | `Cluster` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/postgresql.cnpg.io/cluster_v1.json` |
| `postgresql.cnpg.io/v1` | `Backup` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/postgresql.cnpg.io/backup_v1.json` |
| `postgresql.cnpg.io/v1` | `ScheduledBackup` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/postgresql.cnpg.io/scheduledbackup_v1.json` |
| `barmancloud.cnpg.io/v1` | `ObjectStore` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/barmancloud.cnpg.io/objectstore_v1.json` |
| `longhorn.io/v1beta2` | `RecurringJob` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/longhorn.io/recurringjob_v1beta2.json` |
| `dragonflydb.io/v1alpha1` | `Dragonfly` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/dragonflydb.io/dragonfly_v1alpha1.json` |
| `objectbucket.io/v1alpha1` | `ObjectBucketClaim` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/objectbucket.io/objectbucketclaim_v1alpha1.json` |

### Networking / Cilium

| apiVersion | kind | schema |
|------------|------|--------|
| `cilium.io/v2` | `CiliumNetworkPolicy` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/cilium.io/ciliumnetworkpolicy_v2.json` |
| `cilium.io/v2` | `CiliumLoadBalancerIPPool` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/cilium.io/ciliumloadbalancerippool_v2.json` |
| `cilium.io/v2` | `CiliumBGPClusterConfig` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/cilium.io/ciliumbgpclusterconfig_v2.json` |
| `cilium.io/v2` | `CiliumBGPPeerConfig` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/cilium.io/ciliumbgppeerconfig_v2.json` |
| `cilium.io/v2` | `CiliumBGPAdvertisement` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/cilium.io/ciliumbgpadvertisement_v2.json` |

> The older `CiliumBGPPeeringPolicy` has been retired in this cluster
> in favor of the three-resource model (`ClusterConfig` +
> `PeerConfig` + `Advertisement`). If you see a `BGPPeeringPolicy`
> manifest still in Git, surface it — it's drift, not active config.

### Multus / CNI

| apiVersion | kind | schema |
|------------|------|--------|
| `k8s.cni.cncf.io/v1` | `NetworkAttachmentDefinition` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/k8s.cni.cncf.io/networkattachmentdefinition_v1.json` |

### Node Feature Discovery

| apiVersion | kind | schema |
|------------|------|--------|
| `nfd.k8s-sigs.io/v1alpha1` | `NodeFeatureRule` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/nfd.k8s-sigs.io/nodefeaturerule_v1alpha1.json` |

### External DNS

| apiVersion | kind | schema |
|------------|------|--------|
| `externaldns.k8s.io/v1alpha1` | `DNSEndpoint` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/externaldns.k8s.io/dnsendpoint_v1alpha1.json` |

### Cert-Manager

| apiVersion | kind | schema |
|------------|------|--------|
| `cert-manager.io/v1` | `Certificate` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/cert-manager.io/certificate_v1.json` |
| `cert-manager.io/v1` | `Issuer` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/cert-manager.io/issuer_v1.json` |
| `cert-manager.io/v1` | `ClusterIssuer` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/cert-manager.io/clusterissuer_v1.json` |

### MCP (Kuadrant)

| apiVersion | kind | schema |
|------------|------|--------|
| `mcp.kuadrant.io/v1alpha1` | `MCPServerRegistration` | (no upstream JSON schema yet — append `# TODO: apply schema`) |
| `mcp.kuadrant.io/v1alpha1` | `MCPGatewayExtension` | (no upstream JSON schema yet — append `# TODO: apply schema`) |

### Renovate Operator

| apiVersion | kind | schema |
|------------|------|--------|
| `renovate-operator.mogenius.com/v1alpha1` | `RenovateJob` | (no upstream JSON schema yet — append `# TODO: apply schema`) |
