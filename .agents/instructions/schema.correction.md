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
| `scheduling.k8s.io/v1` | `PriorityClass` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/priorityclass-scheduling-v1.json` |
| `networking.k8s.io/v1` | `NetworkPolicy` | `https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/master-standalone-strict/networkpolicy-networking-v1.json` |

### External Secrets

| apiVersion | kind | schema |
|------------|------|--------|
| `external-secrets.io/v1` | `ExternalSecret` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/external-secrets.io/externalsecret_v1.json` |
| `external-secrets.io/v1` | `ClusterSecretStore` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/external-secrets.io/clustersecretstore_v1.json` |

### Gateway API + Envoy Gateway

| apiVersion | kind | schema |
|------------|------|--------|
| `gateway.networking.k8s.io/v1` | `Gateway` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/gateway.networking.k8s.io/gateway_v1.json` |
| `gateway.networking.k8s.io/v1` | `HTTPRoute` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/gateway.networking.k8s.io/httproute_v1.json` |
| `gateway.envoyproxy.io/v1alpha1` | `EnvoyExtensionPolicy` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/gateway.envoyproxy.io/envoyextensionpolicy_v1alpha1.json` |

### Observability

| apiVersion | kind | schema |
|------------|------|--------|
| `monitoring.coreos.com/v1` | `ServiceMonitor` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/monitoring.coreos.com/servicemonitor_v1.json` |
| `monitoring.coreos.com/v1` | `PodMonitor` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/monitoring.coreos.com/podmonitor_v1.json` |
| `monitoring.coreos.com/v1` | `PrometheusRule` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/monitoring.coreos.com/prometheusrule_v1.json` |

### Storage / Database

| apiVersion | kind | schema |
|------------|------|--------|
| `postgresql.cnpg.io/v1` | `Cluster` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/postgresql.cnpg.io/cluster_v1.json` |
| `postgresql.cnpg.io/v1` | `ScheduledBackup` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/postgresql.cnpg.io/scheduledbackup_v1.json` |
| `barmancloud.cnpg.io/v1` | `ObjectStore` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/barmancloud.cnpg.io/objectstore_v1.json` |
| `longhorn.io/v1beta2` | `RecurringJob` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/longhorn.io/recurringjob_v1beta2.json` |

### Networking / Cilium

| apiVersion | kind | schema |
|------------|------|--------|
| `cilium.io/v2` | `CiliumNetworkPolicy` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/cilium.io/ciliumnetworkpolicy_v2.json` |
| `cilium.io/v2` | `CiliumLoadBalancerIPPool` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/cilium.io/ciliumloadbalancerippool_v2.json` |
| `cilium.io/v2` | `CiliumBGPPeeringPolicy` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/cilium.io/ciliumbgppeeringpolicy_v2.json` |

### Cert-Manager

| apiVersion | kind | schema |
|------------|------|--------|
| `cert-manager.io/v1` | `Certificate` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/cert-manager.io/certificate_v1.json` |
| `cert-manager.io/v1` | `ClusterIssuer` | `https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/cert-manager.io/clusterissuer_v1.json` |

### MCP (Kuadrant)

| apiVersion | kind | schema |
|------------|------|--------|
| `mcp.kuadrant.io/v1alpha1` | `MCPServerRegistration` | (no upstream JSON schema yet — append `# TODO: apply schema`) |
