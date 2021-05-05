#!/bin/bash

. .cluster-secrets.env

echo "######"
echo "# Create namespace"
kubectl --kubeconfig=./kubeconfig create namespace flux-system --dry-run=client -o yaml | kubectl --kubeconfig=./kubeconfig apply -f -

echo "######"
echo "# Create sops-gpg Secret"
gpg --export-secret-keys --armor "${FLUX_KEY_FP}" |
kubectl --kubeconfig=./kubeconfig create secret generic sops-gpg \
    --namespace=flux-system \
    --from-file=sops.asc=/dev/stdin

echo "######"
echo "# 1st Application"
kubectl --kubeconfig=./kubeconfig apply --kustomize=./clusters/lovenet/base/flux-system

echo "######"
echo "# 2nd Application"
kubectl --kubeconfig=./kubeconfig apply --kustomize=./clusters/lovenet/base/flux-system
