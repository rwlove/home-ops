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
echo "Create external-secrets namespace"
kubectl apply -f ./kubernetes/apps/external-secrets/namespace.yaml

echo "# Create Resources"
just bootstrap resources

echo "Create Cluster Settings Configmap"
kubectl -n flux-system apply -f ./kubernetes/flux/meta/cluster-config.yaml

echo "Apply CRDS"
just bootstrap crds

echo "Apply Apps"
just bootstrap apps
