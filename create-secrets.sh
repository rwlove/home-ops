#!/bin/bash

export GPG_TTY=$(tty)

. .cluster-secrets.env

echo "# Create sops-age Secret"
cat ~/.config/sops/age/keys.txt |
    kubectl -n flux-system create secret generic sops-age --from-file=age.agekey=/dev/stdin

echo "Create Cluster Secrets"
envsubst < ./tmpl/cluster-secrets.yaml > ./kubernetes/main/flux/vars/cluster-secrets.yaml
sops --encrypt --in-place ./kubernetes/main/flux/vars/cluster-secrets.yaml

echo "Create cert-manager issuer secrets"
envsubst < ./tmpl/cert-manager-secrets.yaml > ./kubernetes/main/apps/cert-manager/issuers/secrets.yaml
sops --encrypt --in-place ./kubernetes/main/apps/cert-manager/issuers/secrets.yaml

echo "Create External Password (1Password) Secrets"
envsubst < ./kubernetes/main/apps/kube-system/external-secrets/stores/secrets-tmpl.yaml > ./kubernetes/main/apps/kube-system/external-secrets/stores/secrets.yaml
sops --encrypt --in-place ./kubernetes/main/apps/kube-system/external-secrets/stores/secrets.yaml

stg new -m update-secrets
stg refresh --no-verify ; stg pop -a ; git pull ; stg push -a ; stg commit -a ; stg clean ; git push
