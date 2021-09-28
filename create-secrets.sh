#!/bin/bash

export GPG_TTY=$(tty)

. .cluster-secrets.env

envsubst < ./tmpl/.sops.yaml > ./.sops.yaml
envsubst < ./tmpl/cluster-secrets.yaml > ./clusters/lovenet/base/cluster-secrets.yaml
envsubst < ./tmpl/cluster-settings.yaml > ./clusters/lovenet/base/cluster-settings.yaml
envsubst < ./tmpl/gotk-sync.yaml > ./clusters/lovenet/base/flux-system/gotk-sync.yaml
envsubst < ./clusters/lovenet/apps/home/home-assistant/tmpl/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/home-assistant/secrets.yaml
envsubst < ./clusters/lovenet/apps/flux-system/webhook/github/tmpl/secrets-tmpl.yaml > ./clusters/lovenet/apps/flux-system/webhook/github/secrets.yaml
envsubst < ./clusters/lovenet/apps/home/appdaemon/tmpl/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/appdaemon/secrets.yaml
envsubst < ./clusters/lovenet/core/notifications/github/tmpl/secrets-tmpl.yaml > ./clusters/lovenet/core/notifications/github/secrets.yaml
envsubst < ./clusters/lovenet/apps/network/traefik/middlewares/tmpl/secrets-tmpl.yaml > ./clusters/lovenet/apps/network/traefik/middlewares/secrets.yaml
envsubst < ./clusters/lovenet/core/cert-manager/tmpl/secrets-tmpl.yaml > ./clusters/lovenet/core/cert-manager/secrets.yaml
envsubst < ./clusters/lovenet/apps/network/external-dns/tmpl/secrets-tmpl.yaml > ./clusters/lovenet/apps/network/external-dns/secrets.yaml
envsubst < ./clusters/lovenet/apps/network/omada/tmpl/secrets-tmpl.yaml > ./clusters/lovenet/apps/network/omada/secrets.yaml

sops --encrypt ./clusters/lovenet/apps/media/kodidb/tmpl/secrets-tmpl.yaml           > ./clusters/lovenet/apps/media/kodidb/secrets.yaml
sops --encrypt ./clusters/lovenet/apps/media/kodidb/tmpl/mysql-env-secrets-tmpl.yaml > ./clusters/lovenet/apps/media/kodidb/mysql-env.secret.yaml
sops --encrypt ./clusters/lovenet/apps/media/qbittorrent/tmpl/secrets-tmpl.yaml > ./clusters/lovenet/apps/media/qbittorrent/secrets.yaml

sops --encrypt --in-place ./clusters/lovenet/base/cluster-secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/home-assistant/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/flux-system/webhook/github/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/home/appdaemon/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/core/notifications/github/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/network/traefik/middlewares/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/core/cert-manager/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/network/external-dns/secrets.yaml
sops --encrypt --in-place ./clusters/lovenet/apps/network/omada/secrets.yaml
