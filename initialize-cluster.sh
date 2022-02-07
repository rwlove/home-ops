#!/bin/bash

. .cluster-secrets.env

echo "######"
echo "# Create namespace"
kubectl create namespace flux-system --dry-run=client -o yaml | kubectl apply -f -

echo "######"
echo "# Create sops-gpg Secret"
gpg --export-secret-keys --armor "${FLUX_KEY_FP}" |
kubectl create secret generic sops-gpg \
    --namespace=flux-system \
    --from-file=sops.asc=/dev/stdin

#echo "######"
#echo "# 1st Application"
#kubectl apply --kustomize=./clusters/lovenet/base/flux-system

#echo "######"
#echo "# 2nd Application"
#kubectl apply --kustomize=./clusters/lovenet/base/flux-system

# Bootstrap
./bootstrap.sh
