#!/bin/bash

export GPG_TTY=$(tty)

. .cluster-secrets.env

echo "Create Cluster Secrets"
envsubst < ./tmpl/cluster-secrets.yaml > ./clusters/lovenet/base/cluster-secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/base/cluster-secrets.yaml

echo "Create QBittorrent-rss Secrets"
#sops --encrypt ./clusters/lovenet/apps/media/qbittorrent-rss/app/feeds-json-secrets-tmpl.yaml > ./clusters/lovenet/apps/media/qbittorrent-rss/app/feeds-json-secrets.yaml
#sops --encrypt ./clusters/lovenet/apps/media/qbittorrent-rss/app/env-secrets-tmpl.yaml > ./clusters/lovenet/apps/media/qbittorrent-rss/app/env-secrets.yaml

echo "Create External Password (1Password) Secrets"
envsubst < ./clusters/lovenet/apps/kube-system/external-secrets/stores/secrets-tmpl.yaml > ./clusters/lovenet/apps/kube-system/external-secrets/stores/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/kube-system/external-secrets/stores/secrets.yaml
