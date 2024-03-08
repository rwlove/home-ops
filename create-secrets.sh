#!/bin/bash

export GPG_TTY=$(tty)

. .cluster-secrets.env

echo "Create Cluster Secrets"
envsubst < ./tmpl/cluster-secrets.yaml > ./kubernetes/main/flux/vars/cluster-secrets.yaml
sops --encrypt --in-place ./kubernetes/main/flux/vars/cluster-secrets.yaml

echo "Create External Password (1Password) Secrets"
envsubst < ./kubernetes/main/apps/kube-system/external-secrets/stores/secrets-tmpl.yaml > ./kubernetes/main/apps/kube-system/external-secrets/stores/secrets.yaml
sops --encrypt --in-place ./kubernetes/main/apps/kube-system/external-secrets/stores/secrets.yaml
