#!/bin/bash

export GPG_TTY=$(tty)

. .cluster-secrets.env

echo "Create Cluster Secrets"
envsubst < ./tmpl/cluster-secrets.yaml > ./clusters/lovenet/base/cluster-secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/base/cluster-secrets.yaml

echo "Create Cloudnative PG (PostgreSQL) Secrets"
envsubst < ./clusters/lovenet/core/cloudnative-pg/config/secrets-tmpl.yaml > ./clusters/lovenet/core/cloudnative-pg/config/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/core/cloudnative-pg/config/secrets.yaml

echo "Create Authelia Secrets"
envsubst < ./clusters/lovenet/apps/authentication/authelia/secrets-tmpl.yaml > ./clusters/lovenet/apps/authentication/authelia/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/authentication/authelia/secrets.yaml

echo "Create MinIO Secrets"
envsubst < ./clusters/lovenet/core/storage/minio/app/secrets-tmpl.yaml > ./clusters/lovenet/core/storage/minio/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/core/storage/minio/app/secrets.yaml

echo "Create Frigate Secrets"
envsubst < ./clusters/lovenet/apps/home/frigate/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/frigate/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/frigate/secrets.yaml

echo "Create Double-Take Secrets"
envsubst < ./clusters/lovenet/apps/home/double-take/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/double-take/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/double-take/secrets.yaml

echo "Create Home Assistant Secrets"
envsubst < ./clusters/lovenet/apps/home/home-assistant/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/home-assistant/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/home-assistant/app/secrets.yaml

echo "Create Downloads Gateway Secrets"
envsubst < ./clusters/lovenet/core/downloads-gateway/app/secrets-tmpl.yaml > ./clusters/lovenet/core/downloads-gateway/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/core/downloads-gateway/app/secrets.yaml

echo "Create Github Webhook Secrets"
envsubst < ./clusters/lovenet/apps/flux-system/webhook/github/secrets-tmpl.yaml > ./clusters/lovenet/apps/flux-system/webhook/github/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/flux-system/webhook/github/secrets.yaml

echo "Create Github Notification Secrets"
envsubst < ./clusters/lovenet/core/notifications/github/secrets-tmpl.yaml > ./clusters/lovenet/core/notifications/github/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/core/notifications/github/secrets.yaml

echo "Create External DNS Secrets"
envsubst < ./clusters/lovenet/apps/network/external-dns/secrets-tmpl.yaml > ./clusters/lovenet/apps/network/external-dns/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/network/external-dns/secrets.yaml

echo "Create Lidarr Secrets"
envsubst < ./clusters/lovenet/apps/media/lidarr/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/lidarr/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/media/lidarr/secrets.yaml

echo "Create Radarr Secrets"
envsubst < ./clusters/lovenet/apps/media/radarr/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/radarr/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/media/radarr/secrets.yaml

echo "Create Sonarr Secrets"
envsubst < ./clusters/lovenet/apps/media/sonarr/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/sonarr/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/media/sonarr/secrets.yaml

echo "Create Prowlarr Secrets"
envsubst < ./clusters/lovenet/apps/media/prowlarr/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/prowlarr/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/media/prowlarr/secrets.yaml

echo "Create Recyclarr Secrets"
envsubst < ./clusters/lovenet/apps/media/recyclarr/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/recyclarr/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/media/recyclarr/secrets.yaml

echo "Create EMQX (MQTT Broker) Secrets"
envsubst < ./clusters/lovenet/apps/home/emqx/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/emqx/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/emqx/app/secrets.yaml

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

echo "Create QBittorrent-rss Secrets"
sops --encrypt ./clusters/lovenet/apps/media/qbittorrent-rss/feeds-json-secrets-tmpl.yaml > ./clusters/lovenet/apps/media/qbittorrent-rss/feeds-json-secrets.yaml
sops --encrypt ./clusters/lovenet/apps/media/qbittorrent-rss/env-secrets-tmpl.yaml > ./clusters/lovenet/apps/media/qbittorrent-rss/env-secrets.yaml

echo "Create GLAuth Secrets"
sops --encrypt ./clusters/lovenet/apps/authentication/glauth/config/server.toml > clusters/lovenet/apps/authentication/glauth/config/server.sops.toml
sops --encrypt ./clusters/lovenet/apps/authentication/glauth/config/groups.toml > clusters/lovenet/apps/authentication/glauth/config/groups.sops.toml
sops --encrypt ./clusters/lovenet/apps/authentication/glauth/config/users.toml > clusters/lovenet/apps/authentication/glauth/config/users.sops.toml

echo "Create pgadmin Secrets"
envsubst < ./clusters/lovenet/apps/databases/pgadmin/secrets-tmpl.yaml > ./clusters/lovenet/apps/databases/pgadmin/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/databases/pgadmin/secrets.yaml

echo "Create Mastodon Secrets"
envsubst < ./clusters/lovenet/apps/social-media/mastodon/secrets-tmpl.yaml > ./clusters/lovenet/apps/social-media/mastodon/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/social-media/mastodon/secrets.yaml

echo "Create Statping Secrets"
envsubst < ./clusters/lovenet/apps/monitoring/statping/secrets-tmpl.yaml > ./clusters/lovenet/apps/monitoring/statping/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/monitoring/statping/secrets.yaml

echo "Create Vaultwarden Secrets"
envsubst < ./clusters/lovenet/apps/home/vaultwarden/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/vaultwarden/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/vaultwarden/secrets.yaml

echo "Create SMTP Relay Secrets"
envsubst < ./clusters/lovenet/apps/home/smtp-relay/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/smtp-relay/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/smtp-relay/secrets.yaml

echo "Create NextCloud Secrets"
envsubst < ./clusters/lovenet/apps/home/nextcloud/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/nextcloud/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/nextcloud/secrets.yaml

echo "Create Sabnzbd Secrets"
envsubst < ./clusters/lovenet/apps/media/sabnzbd/app/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/sabnzbd/app/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/media/sabnzbd/app/secrets.yaml

echo "Create Kodi Mariadb (mariadb-operator)"
sops --encrypt ./clusters/lovenet/apps/media/kodi-mariadb/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/kodi-mariadb/secrets.yaml
