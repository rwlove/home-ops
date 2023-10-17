#!/bin/bash

export GPG_TTY=$(tty)

. .cluster-secrets.env

echo "Create Cluster Secrets"
envsubst < ./tmpl/cluster-secrets.yaml > ./clusters/lovenet/base/cluster-secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/base/cluster-secrets.yaml

echo "Create Cloudnative PG (PostgreSQL) Secrets"
envsubst < ./clusters/lovenet/apps/databases/cloudnative-pg/config/secrets-tmpl.yaml > ./clusters/lovenet/apps/databases/cloudnative-pg/config/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/databases/cloudnative-pg/config/secrets.yaml

echo "Create Frigate Secrets"
envsubst < ./clusters/lovenet/apps/home/frigate/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/frigate/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/frigate/app/secrets.yaml

echo "Create Double-Take Secrets"
envsubst < ./clusters/lovenet/apps/home/double-take/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/double-take/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/double-take/app/secrets.yaml

echo "Create Downloads Gateway Secrets"
envsubst < ./clusters/lovenet/apps/network/downloads-gateway/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/network/downloads-gateway/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/network/downloads-gateway/app/secrets.yaml

echo "Create Github Webhook Secrets"
envsubst < ./clusters/lovenet/apps/flux-system/webhook/github/secrets-tmpl.yaml > ./clusters/lovenet/apps/flux-system/webhook/github/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/flux-system/webhook/github/secrets.yaml

echo "Create Github Notification Secrets"
envsubst < ./clusters/lovenet/apps/notifications/github/secrets-tmpl.yaml > ./clusters/lovenet/apps/notifications/github/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/notifications/github/secrets.yaml

echo "Create External DNS Secrets"
envsubst < ./clusters/lovenet/apps/network/external-dns/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/network/external-dns/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/network/external-dns/app/secrets.yaml

echo "Create EMQX (MQTT Broker) Secrets"
envsubst < ./clusters/lovenet/apps/home/emqx/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/emqx/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/emqx/app/secrets.yaml

echo "Create Vector GeoIPUpdate Secrets"
envsubst < ./clusters/lovenet/apps/monitoring/vector/aggregator/secrets-tmpl.yaml > ./clusters/lovenet/apps/monitoring/vector/aggregator/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/monitoring/vector/aggregator/secrets.yaml

echo "Create Cloudflare DDNS Secrets"
envsubst < ./clusters/lovenet/apps/network/cloudflare-ddns/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/network/cloudflare-ddns/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/network/cloudflare-ddns/app/secrets.yaml

echo "Create QBittorrent-rss Secrets"
#sops --encrypt ./clusters/lovenet/apps/media/qbittorrent-rss/app/feeds-json-secrets-tmpl.yaml > ./clusters/lovenet/apps/media/qbittorrent-rss/app/feeds-json-secrets.yaml
#sops --encrypt ./clusters/lovenet/apps/media/qbittorrent-rss/app/env-secrets-tmpl.yaml > ./clusters/lovenet/apps/media/qbittorrent-rss/app/env-secrets.yaml

echo "Create SMTP Relay Secrets"
envsubst < ./clusters/lovenet/apps/home/smtp-relay/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/smtp-relay/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/smtp-relay/app/secrets.yaml

echo "Create NextCloud Secrets"
envsubst < ./clusters/lovenet/apps/home/nextcloud/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/nextcloud/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/nextcloud/app/secrets.yaml

echo "Create Mopidy Secrets"
envsubst < ./clusters/lovenet/apps/media/radio/mopidy/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/radio/mopidy/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/media/radio/mopidy/app/secrets.yaml

echo "Create External Password (1Password) Secrets"
envsubst < ./clusters/lovenet/apps/kube-system/external-secrets/stores/secrets-tmpl.yaml > ./clusters/lovenet/apps/kube-system/external-secrets/stores/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/kube-system/external-secrets/stores/secrets.yaml
