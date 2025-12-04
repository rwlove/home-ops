#!/bin/bash

set -eu -o pipefail

scp root@master1:~/.kube/config ~/.kube/config

#echo "######"
echo "Create flux-system namespace"
kubectl apply -f ./kubernetes/apps/flux-system/namespace.yaml
echo "Create observability namespace"
kubectl apply -f ./kubernetes/apps/observability/namespace.yaml
echo "Create network namespace"
kubectl apply -f ./kubernetes/apps/network/namespace.yaml
echo "Create cert-manager namespace"
kubectl apply -f ./kubernetes/apps/cert-manager/namespace.yaml

echo "# Create Resources"
just -f bootstrap/mod.just

echo "Create Cluster Settings Configmap"
kubectl apply -f ./kubernetes/apps/flux-system/flux-instance/app/cluster-config.yaml

echo "Apply CRDS"
helmfile -f "bootstrap/helmfile.d/00-crds.yaml" template -q | kubectl apply --server-side --field-manager bootstrap --force-conflicts -f -

echo "Apply Apps"
helmfile -f "bootstrap/helmfile.d/01-apps.yaml" sync --hide-notes
