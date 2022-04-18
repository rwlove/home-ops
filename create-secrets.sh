#!/bin/bash

export GPG_TTY=$(tty)

. .cluster-secrets.env

# Cluster Secrets
envsubst < ./tmpl/cluster-secrets.yaml > ./clusters/lovenet/base/cluster-secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/base/cluster-secrets.yaml

# Frigate Secrets
envsubst < ./clusters/lovenet/apps/home/frigate/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/frigate/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/frigate/secrets.yaml

# Home Assistant Secrets
envsubst < ./clusters/lovenet/apps/home/home-assistant/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/home-assistant/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/home-assistant/secrets.yaml

# Github Webhook Secrets
envsubst < ./clusters/lovenet/apps/flux-system/webhook/github/secrets-tmpl.yaml > ./clusters/lovenet/apps/flux-system/webhook/github/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/flux-system/webhook/github/secrets.yaml

# Appademon Secrets
envsubst < ./clusters/lovenet/apps/home/appdaemon/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/appdaemon/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/appdaemon/secrets.yaml

# Github Notification Secrets
envsubst < ./clusters/lovenet/core/notifications/github/secrets-tmpl.yaml > ./clusters/lovenet/core/notifications/github/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/core/notifications/github/secrets.yaml

# External DNS Secrets
envsubst < ./clusters/lovenet/apps/network/external-dns/secrets-tmpl.yaml > ./clusters/lovenet/apps/network/external-dns/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/network/external-dns/secrets.yaml

# Kodi Secrets
envsubst < ./clusters/lovenet/apps/media/kodidb/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/kodidb/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/media/kodidb/secrets.yaml

# Qbittorrent Secrets (TODO: move vpn configuration into .cluster-secrets.yaml so that secrets-tmpl.yaml can be committed)
sops --encrypt ./clusters/lovenet/apps/media/qbittorrent/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/qbittorrent/secrets.yaml

# Qbittorrent-rss Secrets
sops --encrypt ./clusters/lovenet/apps/media/qbittorrent-rss/feeds-json-secrets-tmpl.yaml > ./clusters/lovenet/apps/media/qbittorrent-rss/feeds-json-secrets.yaml
sops --encrypt ./clusters/lovenet/apps/media/qbittorrent-rss/env-secrets-tmpl.yaml > ./clusters/lovenet/apps/media/qbittorrent-rss/env-secrets.yaml
