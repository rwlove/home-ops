#!/bin/bash

set -eu -o pipefail

scp root@master1:~/.kube/config ~/.kube/config

#echo "######"
echo "Create flux-system namespace"
kubectl apply -f ./kubernetes/apps/namespaces/flux-system.yaml
echo "Create observability namespace"
kubectl apply -f ./kubernetes/apps/namespaces/observability.yaml
echo "Create network namespace"
kubectl apply -f ./kubernetes/apps/namespaces/network.yaml

echo "# Create Secrets Patch and Push"
./create-secrets.sh

echo "Create Cluster Settings Configmap"
kubectl apply -f ./kubernetes/apps/flux-system/flux-instance/app/cluster-settings.yaml

echo "Apply CRDS"
helmfile -f "bootstrap/helmfile.d/00-crds.yaml" template -q | kubectl apply --server-side --field-manager bootstrap --force-conflicts -f -

echo "Apply Apps"
helmfile -f "bootstrap/helmfile.d/01-apps.yaml" sync --hide-notes
