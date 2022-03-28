#!/bin/bash

export GPG_TTY=$(tty)

. .cluster-secrets.env

# Cluster Secrets
envsubst < ./tmpl/cluster-secrets.yaml > ./clusters/lovenet/base/cluster-secrets.yaml

# Frigate Secrets
envsubst < ./clusters/lovenet/apps/home/frigate/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/frigate/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/frigate/secrets.yaml

# Home Assistant Secrets
envsubst < ./clusters/lovenet/apps/home/home-assistant/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/home-assistant/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/home-assistant/secrets.yaml




envsubst < ./clusters/lovenet/apps/flux-system/webhook/github/tmpl/secrets-tmpl.yaml > ./clusters/lovenet/apps/flux-system/webhook/github/secrets.yaml
envsubst < ./clusters/lovenet/apps/home/appdaemon/tmpl/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/appdaemon/secrets.yaml
envsubst < ./clusters/lovenet/core/notifications/github/tmpl/secrets-tmpl.yaml > ./clusters/lovenet/core/notifications/github/secrets.yaml
envsubst < ./clusters/lovenet/apps/network/traefik/middlewares/tmpl/secrets-tmpl.yaml > ./clusters/lovenet/apps/network/traefik/middlewares/secrets.yaml
envsubst < ./clusters/lovenet/apps/network/external-dns/tmpl/secrets-tmpl.yaml > ./clusters/lovenet/apps/network/external-dns/secrets.yaml
envsubst < ./clusters/lovenet/apps/cert-manager/issuers/tmpl/secrets-tmpl.yaml > ./clusters/lovenet/apps/cert-manager/issuers/secrets.yaml

sops --encrypt ./clusters/lovenet/apps/media/kodidb/tmpl/secrets-tmpl.yaml           > ./clusters/lovenet/apps/media/kodidb/secrets.yaml
sops --encrypt ./clusters/lovenet/apps/media/qbittorrent/tmpl/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/qbittorrent/secrets.yaml

sops --encrypt --in-place ./clusters/lovenet/base/cluster-secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/home-assistant/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/flux-system/webhook/github/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/appdaemon/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/core/notifications/github/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/network/traefik/middlewares/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/network/external-dns/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/cert-manager/issuers/secrets.yaml

