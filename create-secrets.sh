#!/bin/bash

export GPG_TTY=$(tty)

. .cluster-secrets.env

echo "Create Cluster Secrets"
envsubst < ./tmpl/cluster-secrets.yaml > ./clusters/lovenet/base/cluster-secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/base/cluster-secrets.yaml

echo "Create Cloudnative PG (PostgreSQL) Secrets"
envsubst < ./clusters/lovenet/apps/databases/cloudnative-pg/config/secrets-tmpl.yaml > ./clusters/lovenet/apps/databases/cloudnative-pg/config/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/databases/cloudnative-pg/config/secrets.yaml

echo "Create MinIO Secrets"
envsubst < ./clusters/lovenet/apps/storage/minio/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/storage/minio/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/storage/minio/app/secrets.yaml

echo "Create Frigate Secrets"
envsubst < ./clusters/lovenet/apps/home/frigate/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/frigate/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/frigate/app/secrets.yaml

echo "Create Double-Take Secrets"
envsubst < ./clusters/lovenet/apps/home/double-take/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/double-take/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/double-take/app/secrets.yaml

echo "Create Home Assistant Secrets"
envsubst < ./clusters/lovenet/apps/home/home-assistant/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/home-assistant/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/home-assistant/app/secrets.yaml

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

echo "Create Lidarr Secrets"
envsubst < ./clusters/lovenet/apps/media/lidarr/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/lidarr/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/media/lidarr/app/secrets.yaml

echo "Create Radarr Secrets"
envsubst < ./clusters/lovenet/apps/media/radarr/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/radarr/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/media/radarr/app/secrets.yaml

echo "Create Sonarr Secrets"
envsubst < ./clusters/lovenet/apps/media/sonarr/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/sonarr/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/media/sonarr/app/secrets.yaml

echo "Create Prowlarr Secrets"
envsubst < ./clusters/lovenet/apps/media/prowlarr/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/prowlarr/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/media/prowlarr/app/secrets.yaml

echo "Create Recyclarr Secrets"
envsubst < ./clusters/lovenet/apps/media/recyclarr/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/recyclarr/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/media/recyclarr/app/secrets.yaml

echo "Create EMQX (MQTT Broker) Secrets"
envsubst < ./clusters/lovenet/apps/home/emqx/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/emqx/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/emqx/app/secrets.yaml

echo "Create Grafana Secrets"
envsubst < ./clusters/lovenet/apps/monitoring/grafana/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/monitoring/grafana/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/monitoring/grafana/app/secrets.yaml

echo "Create Vector GeoIPUpdate Secrets"
envsubst < ./clusters/lovenet/apps/monitoring/vector/aggregator/secrets-tmpl.yaml > ./clusters/lovenet/apps/monitoring/vector/aggregator/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/monitoring/vector/aggregator/secrets.yaml

echo "Create Cloudflare DDNS Secrets"
envsubst < ./clusters/lovenet/apps/network/cloudflare-ddns/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/network/cloudflare-ddns/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/network/cloudflare-ddns/app/secrets.yaml

echo "Create QBittorrent-rss Secrets"
#sops --encrypt ./clusters/lovenet/apps/media/qbittorrent-rss/app/feeds-json-secrets-tmpl.yaml > ./clusters/lovenet/apps/media/qbittorrent-rss/app/feeds-json-secrets.yaml
#sops --encrypt ./clusters/lovenet/apps/media/qbittorrent-rss/app/env-secrets-tmpl.yaml > ./clusters/lovenet/apps/media/qbittorrent-rss/app/env-secrets.yaml

echo "Create GLAuth Secrets"
sops --encrypt ./clusters/lovenet/apps/authentication/glauth/app/config/server.toml > clusters/lovenet/apps/authentication/glauth/app/config/server.sops.toml
sops --encrypt ./clusters/lovenet/apps/authentication/glauth/app/config/groups.toml > clusters/lovenet/apps/authentication/glauth/app/config/groups.sops.toml
sops --encrypt ./clusters/lovenet/apps/authentication/glauth/app/config/users.toml > clusters/lovenet/apps/authentication/glauth/app/config/users.sops.toml

echo "Create pgadmin Secrets"
envsubst < ./clusters/lovenet/apps/databases/pgadmin/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/databases/pgadmin/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/databases/pgadmin/app/secrets.yaml

echo "Create SMTP Relay Secrets"
envsubst < ./clusters/lovenet/apps/home/smtp-relay/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/smtp-relay/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/smtp-relay/app/secrets.yaml

echo "Create K8SGPT Secrets"
envsubst < ./clusters/lovenet/apps/ai/k8sgpt/config/secrets-tmpl.yaml > ./clusters/lovenet/apps/ai/k8sgpt/config/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/ai/k8sgpt/config/secrets.yaml

echo "Create NextCloud Secrets"
envsubst < ./clusters/lovenet/apps/home/nextcloud/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/nextcloud/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/nextcloud/app/secrets.yaml

echo "Create Sabnzbd Secrets"
envsubst < ./clusters/lovenet/apps/media/sabnzbd/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/sabnzbd/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/media/sabnzbd/app/secrets.yaml

echo "Create Immich Secrets"
envsubst < ./clusters/lovenet/apps/media/immich/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/immich/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/media/immich/app/secrets.yaml

echo "Create Mopidy Secrets"
envsubst < ./clusters/lovenet/apps/media/radio/mopidy/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/radio/mopidy/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/media/radio/mopidy/app/secrets.yaml

echo "Create Autobrr Secrets"
envsubst < ./clusters/lovenet/apps/downloads/autobrr/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/downloads/autobrr/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/downloads/autobrr/app/secrets.yaml

echo "Create actions-runner-controller Secrets"
envsubst < ./clusters/lovenet/apps/actions-runner-system/actions-runner-controller/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/actions-runner-system/actions-runner-controller/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/actions-runner-system/actions-runner-controller/app/secrets.yaml

echo "Create External Password (1Password) Secrets"
envsubst < ./clusters/lovenet/apps/kube-system/external-secrets/stores/secrets-tmpl.yaml > ./clusters/lovenet/apps/kube-system/external-secrets/stores/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/kube-system/external-secrets/stores/secrets.yaml
