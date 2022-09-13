#!/bin/bash

. .config.env
. .cluster-secrets.env

envsubst < "./tmpl/cluster-settings.yaml" \
         > "./clusters/lovenet/base/cluster-settings.yaml"

envsubst < "./tmpl/gotk-sync.yaml" \
         > "./clusters/lovenet/base/flux-system/gotk-sync.yaml"

envsubst < "./tmpl/kube-vip-daemonset.yaml" \
         > "./clusters/lovenet/core/kube-system/kube-vip/daemon-set.yaml"

envsubst < "./tmpl/cluster-secrets.yaml" \
         > "./clusters/lovenet/base/cluster-secrets.yaml"

sops --encrypt --in-place "./clusters/lovenet/base/cluster-secrets.yaml"

envsubst < "./tmpl/cert-manager-secrets.yaml" \
         > "./clusters/lovenet/core/cert-manager/issuers/secrets.yaml"

sops --encrypt --in-place "./clusters/lovenet/core/cert-manager/issuers/secrets.yaml"

echo "######"
echo "# Create namespace"
kubectl create namespace flux-system --dry-run=client -o yaml | kubectl apply -f -

echo "######"
echo "# Create sops-age Secret"
cat ~/.config/sops/age/keys.txt |
    kubectl -n flux-system create secret generic sops-age \
    --from-file=age.agekey=/dev/stdin

echo "######"
echo "# 1st Application"
kubectl apply --kustomize=./clusters/lovenet/base/flux-system

echo "######"
echo "# 2nd Application"
kubectl apply --kustomize=./clusters/lovenet/base/flux-system


echo "######"
echo "# Create Secrets "
stg new -m update-secrets
./create-secrets.sh
stg refresh --no-verify ; stg pop -a ; git pull ; stg push -a ; stg commit -a ; stg clean ; git push
