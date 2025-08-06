#!/bin/bash

. .cluster-secrets.env

echo "Deploy Flux"
kubectl apply --server-side --kustomize ./kubernetes/main/bootstrap/flux

#echo "Create cluster-settings"
#envsubst < "./tmpl/cluster-settings.yaml" \
#         > "./kubernetes/main/flux/vars/cluster-settings.yaml"

#envsubst < "./tmpl/gotk-sync.yaml" \
#         > "./kubernetes/main/base/flux-system/gotk-sync.yaml"

#echo "Create cluster-secrets"
#envsubst < "./tmpl/cluster-secrets.yaml" \
#         > "./kubernetes/main/flux/vars/cluster-secrets.yaml"

#echo "Encrypt cluster-secrets"
#sops --encrypt --in-place "./kubernetes/main/flux/vars/cluster-secrets.yaml"

echo "Create cert-manager issuer secrets"
envsubst < "./tmpl/cert-manager-secrets.yaml" \
         > "./kubernetes/main/apps/cert-manager/issuers/secrets.yaml"

echo "Encrypt cert-manager issuer secrets"
sops --encrypt --in-place "./kubernetes/main/apps/cert-manager/issuers/secrets.yaml"

## TODO: Where does this get done in the new flow? Remove if working.
#echo "######"
#echo "# Create namespace"
#kubectl create namespace flux-system --dry-run=client -o yaml | kubectl apply -f -

#echo "######"
echo "# Create sops-age Secret"
cat ~/.config/sops/age/keys.txt |
    kubectl -n flux-system create secret generic sops-age \
    --from-file=age.agekey=/dev/stdin

#echo "######"
#echo "# 1st Application"
#kubectl apply --kustomize=./kubernetes/main/base/flux-system

#echo "######"
#echo "# 2nd Application"
#kubectl apply --kustomize=./kubernetes/main/base/flux-system

#echo "######"
echo "# Create Secrets Patch and Push"
stg new -m update-secrets
./create-secrets.sh
stg refresh --no-verify ; stg pop -a ; git pull ; stg push -a ; stg commit -a ; stg clean ; git push

echo "Create Cluster Settings Configmap"
kubectl apply -f ./kubernetes/main/flux/vars/cluster-settings.yaml

echo "Create Cluster Secrets"
sops --decrypt ./kubernetes/main/flux/vars/cluster-secrets.yaml | kubectl apply -f -

echo "Bootstrap CRDs"
kubectl apply -k ./kubernetes/main/bootstrap/crds

echo "Create Cluster"
kubectl apply --server-side --kustomize ./kubernetes/main/flux/config
