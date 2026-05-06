set quiet := true
set shell := ['bash', '-euo', 'pipefail', '-c']

[private]
default:
    just -l k8s

[doc('Browse a PVC interactively')]
browse-pvc namespace claim:
    kubectl browse-pvc -n {{ namespace }} -i mirror.gcr.io/alpine:latest {{ claim }}

[doc('Open a shell on a node')]
node-shell node:
    kubectl debug node/{{ node }} -n default -it --image='mirror.gcr.io/alpine:latest' --profile sysadmin
    kubectl delete pod -n default -l app.kubernetes.io/managed-by=kubectl-debug

[doc('Drop a netshoot pod for ad-hoc network debugging')]
netshoot:
    kubectl run -it --rm netshoot --image=ghcr.io/nicolaka/netshoot --restart=Never -- /bin/bash

[doc('Prune pods in Failed, Pending, or Succeeded state')]
prune-pods:
    for phase in Failed Pending Succeeded; do \
      kubectl delete pods -A --field-selector status.phase="$phase" --ignore-not-found=true; \
    done

[doc('Force-reconcile a single Flux HelmRelease')]
sync-hr ns name:
    kubectl -n "{{ ns }}" annotate --field-manager flux-client-side-apply --overwrite hr "{{ name }}" \
      reconcile.fluxcd.io/requestedAt="$(date +%s)" \
      reconcile.fluxcd.io/forceAt="$(date +%s)"

[doc('Force-reconcile a single Flux Kustomization')]
sync-ks ns name:
    kubectl -n "{{ ns }}" annotate --field-manager flux-client-side-apply --overwrite ks "{{ name }}" \
      reconcile.fluxcd.io/requestedAt="$(date +%s)"

[doc('Force-resync a single ExternalSecret')]
sync-es ns name:
    kubectl -n "{{ ns }}" annotate --field-manager flux-client-side-apply --overwrite es "{{ name }}" \
      force-sync="$(date +%s)"

[doc('Force-reconcile every HelmRelease in the cluster')]
sync-all-hr:
    kubectl get hr --no-headers -A | while read -r ns name _; do \
      just k8s sync-hr "$ns" "$name"; \
    done

[doc('Force-reconcile every Kustomization in the cluster')]
sync-all-ks:
    kubectl get ks --no-headers -A | while read -r ns name _; do \
      just k8s sync-ks "$ns" "$name"; \
    done

[doc('Force-resync every ExternalSecret in the cluster')]
sync-all-es:
    kubectl get es --no-headers -A | while read -r ns name _; do \
      just k8s sync-es "$ns" "$name"; \
    done

[doc('Tail recent cluster events sorted by time')]
events ns="":
    if [[ -n "{{ ns }}" ]]; then \
      kubectl get events -n "{{ ns }}" --sort-by=.lastTimestamp; \
    else \
      kubectl get events -A --sort-by=.lastTimestamp; \
    fi

[doc('Print CNPG cluster status across the databases namespace')]
pg-status:
    kubectl get cluster.postgresql.cnpg.io -n databases \
      -o custom-columns=NAME:.metadata.name,INSTANCES:.spec.instances,READY:.status.readyInstances,STATUS:.status.phase

[doc('Trigger an on-demand backup for one CNPG cluster')]
pg-backup-now app:
    printf '%s\n' \
      'apiVersion: postgresql.cnpg.io/v1' \
      'kind: Backup' \
      'metadata:' \
      '  generateName: postgres-{{ app }}-manual-' \
      'spec:' \
      '  cluster:' \
      '    name: postgres-{{ app }}' \
      '  method: plugin' \
      '  pluginConfiguration:' \
      '    name: barman-cloud.cloudnative-pg.io' \
      | kubectl -n databases create -f -
