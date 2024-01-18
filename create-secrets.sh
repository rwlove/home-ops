#!/bin/bash

export GPG_TTY=$(tty)

. .cluster-secrets.env

echo "Create Cluster Secrets"
envsubst < ./tmpl/cluster-secrets.yaml > ./kubernetes/main/base/cluster-secrets.yaml
sops --encrypt --in-place ./kubernetes/main/base/cluster-secrets.yaml

echo "Create QBittorrent-rss Secrets"
#sops --encrypt ./kubernetes/main/apps/media/qbittorrent-rss/app/feeds-json-secrets-tmpl.yaml > ./kubernetes/main/apps/media/qbittorrent-rss/app/feeds-json-secrets.yaml
#sops --encrypt ./kubernetes/main/apps/media/qbittorrent-rss/app/env-secrets-tmpl.yaml > ./kubernetes/main/apps/media/qbittorrent-rss/app/env-secrets.yaml

echo "Create External Password (1Password) Secrets"
envsubst < ./kubernetes/main/apps/kube-system/external-secrets/stores/secrets-tmpl.yaml > ./kubernetes/main/apps/kube-system/external-secrets/stores/secrets.yaml
sops --encrypt --in-place ./kubernetes/main/apps/kube-system/external-secrets/stores/secrets.yaml
