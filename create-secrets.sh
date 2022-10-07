#!/bin/bash

export GPG_TTY=$(tty)

. .cluster-secrets.env

# Cluster Secrets
envsubst < ./tmpl/cluster-secrets.yaml > ./clusters/lovenet/base/cluster-secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/base/cluster-secrets.yaml

# PostgreSQL Secrets
envsubst < ./clusters/lovenet/apps/databases/postgresql/secrets-tmpl.yaml > ./clusters/lovenet/apps/databases/postgresql/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/databases/postgresql/secrets.yaml

# Authelia Secrets
envsubst < ./clusters/lovenet/apps/authentication/authelia/secrets-tmpl.yaml > ./clusters/lovenet/apps/authentication/authelia/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/authentication/authelia/secrets.yaml

# MinIO Secrets
envsubst < ./clusters/lovenet/apps/storage/minio/secrets-tmpl.yaml > ./clusters/lovenet/apps/storage/minio/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/storage/minio/secrets.yaml

# Frigate Secrets
envsubst < ./clusters/lovenet/apps/home/frigate/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/frigate/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/frigate/secrets.yaml

# OwnCloud OCIS Secrets
envsubst < ./clusters/lovenet/apps/home/owncloud-ocis/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/owncloud-ocis/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/owncloud-ocis/secrets.yaml

# Mopidy Secrets
envsubst < ./clusters/lovenet/apps/media/mopidy/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/mopidy/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/media/mopidy/secrets.yaml

# Home Assistant Secrets
envsubst < ./clusters/lovenet/apps/home/home-assistant/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/home-assistant/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/home-assistant/secrets.yaml

# Downloads Gateway Secrets
envsubst < ./clusters/lovenet/core/downloads-gateway/secrets-tmpl.yaml > ./clusters/lovenet/core/downloads-gateway/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/core/downloads-gateway/secrets.yaml

# Github Webhook Secrets
envsubst < ./clusters/lovenet/apps/flux-system/webhook/github/secrets-tmpl.yaml > ./clusters/lovenet/apps/flux-system/webhook/github/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/flux-system/webhook/github/secrets.yaml

# Github Notification Secrets
envsubst < ./clusters/lovenet/core/notifications/github/secrets-tmpl.yaml > ./clusters/lovenet/core/notifications/github/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/core/notifications/github/secrets.yaml

# External DNS Secrets
envsubst < ./clusters/lovenet/apps/network/external-dns/secrets-tmpl.yaml > ./clusters/lovenet/apps/network/external-dns/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/network/external-dns/secrets.yaml

# Kodi Secrets
envsubst < ./clusters/lovenet/apps/media/kodidb/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/kodidb/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/media/kodidb/secrets.yaml

# Grafana #
envsubst < ./clusters/lovenet/apps/monitoring/grafana/secrets-tmpl.yaml > ./clusters/lovenet/apps/monitoring/grafana/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/monitoring/grafana/secrets.yaml

# GeoIPUpdate Secrets
envsubst < ./clusters/lovenet/core/monitoring/vector/geoipupdate/secrets-tmpl.yaml > ./clusters/lovenet/core/monitoring/vector/geoipupdate/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/core/monitoring/vector/geoipupdate/secrets.yaml

# Cloudflare DDNS #
envsubst < ./clusters/lovenet/apps/network/cloudflare-ddns/secrets-tmpl.yaml > ./clusters/lovenet/apps/network/cloudflare-ddns/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/network/cloudflare-ddns/secrets.yaml

# Qbittorrent Secrets (TODO: move vpn configuration into .cluster-secrets.yaml so that secrets-tmpl.yaml can be committed)
sops --encrypt ./clusters/lovenet/apps/media/qbittorrent/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/qbittorrent/secrets.yaml

# Qbittorrent-rss Secrets
sops --encrypt ./clusters/lovenet/apps/media/qbittorrent-rss/feeds-json-secrets-tmpl.yaml > ./clusters/lovenet/apps/media/qbittorrent-rss/feeds-json-secrets.yaml
sops --encrypt ./clusters/lovenet/apps/media/qbittorrent-rss/env-secrets-tmpl.yaml > ./clusters/lovenet/apps/media/qbittorrent-rss/env-secrets.yaml
