#!/bin/bash

export GPG_TTY=$(tty)

. .cluster-secrets.env

echo "Create Cluster Secrets"
envsubst < ./tmpl/cluster-secrets.yaml > ./clusters/lovenet/base/cluster-secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/base/cluster-secrets.yaml

echo "Create Cloudnative PG (PostgreSQL) Secrets"
envsubst < clusters/lovenet/apps/databases/cloudnative-pg/config/secrets-tmpl.yaml > ./clusters/lovenet/apps/databases/cloudnative-pg/config/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/databases/cloudnative-pg/config/secrets.yaml

echo "Create Authelia Secrets"
envsubst < ./clusters/lovenet/apps/authentication/authelia/secrets-tmpl.yaml > ./clusters/lovenet/apps/authentication/authelia/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/authentication/authelia/secrets.yaml

echo "Create MinIO Secrets"
envsubst < ./clusters/lovenet/apps/storage/minio/secrets-tmpl.yaml > ./clusters/lovenet/apps/storage/minio/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/storage/minio/secrets.yaml

echo "Create Frigate Secrets"
envsubst < ./clusters/lovenet/apps/home/frigate/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/frigate/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/frigate/secrets.yaml

echo "Create OwnCloud OCIS Secrets"
envsubst < ./clusters/lovenet/apps/home/owncloud-ocis/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/owncloud-ocis/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/owncloud-ocis/secrets.yaml

echo "Create Home Assistant Secrets"
envsubst < ./clusters/lovenet/apps/home/home-assistant/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/home-assistant/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/home-assistant/secrets.yaml

echo "Create Downloads Gateway Secrets"
envsubst < ./clusters/lovenet/core/downloads-gateway/secrets-tmpl.yaml > ./clusters/lovenet/core/downloads-gateway/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/core/downloads-gateway/secrets.yaml

echo "Create Github Webhook Secrets"
envsubst < ./clusters/lovenet/apps/flux-system/webhook/github/secrets-tmpl.yaml > ./clusters/lovenet/apps/flux-system/webhook/github/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/flux-system/webhook/github/secrets.yaml

echo "Create Github Notification Secrets"
envsubst < ./clusters/lovenet/core/notifications/github/secrets-tmpl.yaml > ./clusters/lovenet/core/notifications/github/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/core/notifications/github/secrets.yaml

echo "Create External DNS Secrets"
envsubst < ./clusters/lovenet/apps/network/external-dns/secrets-tmpl.yaml > ./clusters/lovenet/apps/network/external-dns/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/network/external-dns/secrets.yaml

echo "Create Kodi Secrets"
envsubst < ./clusters/lovenet/apps/media/kodidb/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/kodidb/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/media/kodidb/secrets.yaml

echo "Create Lidarr Secrets"
envsubst < ./clusters/lovenet/apps/media/lidarr/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/lidarr/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/media/lidarr/secrets.yaml

echo "Create Grafana Secrets"
envsubst < ./clusters/lovenet/apps/monitoring/grafana/secrets-tmpl.yaml > ./clusters/lovenet/apps/monitoring/grafana/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/monitoring/grafana/secrets.yaml

echo "Create Vikunja Secrets"
envsubst < ./clusters/lovenet/apps/collab/vikunja/secrets-tmpl.yaml > ./clusters/lovenet/apps/collab/vikunja/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/collab/vikunja/secrets.yaml

echo "Create Vector GeoIPUpdate Secrets"
envsubst < ./clusters/lovenet/core/monitoring/vector/geoipupdate/secrets-tmpl.yaml > ./clusters/lovenet/core/monitoring/vector/geoipupdate/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/core/monitoring/vector/geoipupdate/secrets.yaml

echo "Create Cloudflare DDNS Secrets"
envsubst < ./clusters/lovenet/apps/network/cloudflare-ddns/secrets-tmpl.yaml > ./clusters/lovenet/apps/network/cloudflare-ddns/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/network/cloudflare-ddns/secrets.yaml

echo "Create Qbittorrent Secrets (TODO: move vpn configuration into .cluster-secrets.yaml so that secrets-tmpl.yaml can be committed)"
sops --encrypt ./clusters/lovenet/apps/media/qbittorrent/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/qbittorrent/secrets.yaml

echo "Create Qbittorrent-rss Secrets"
sops --encrypt ./clusters/lovenet/apps/media/qbittorrent-rss/feeds-json-secrets-tmpl.yaml > ./clusters/lovenet/apps/media/qbittorrent-rss/feeds-json-secrets.yaml
sops --encrypt ./clusters/lovenet/apps/media/qbittorrent-rss/env-secrets-tmpl.yaml > ./clusters/lovenet/apps/media/qbittorrent-rss/env-secrets.yaml

echo "Create GLAuth Secrets"
sops --encrypt ./clusters/lovenet/apps/authentication/glauth/config/server.toml > clusters/lovenet/apps/authentication/glauth/config/server.sops.toml
sops --encrypt ./clusters/lovenet/apps/authentication/glauth/config/groups.toml > clusters/lovenet/apps/authentication/glauth/config/groups.sops.toml
sops --encrypt ./clusters/lovenet/apps/authentication/glauth/config/users.toml > clusters/lovenet/apps/authentication/glauth/config/users.sops.toml
