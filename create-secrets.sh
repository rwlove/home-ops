#!/bin/bash

export GPG_TTY=$(tty)

. .cluster-secrets.env

. .config.env

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

age -e -r ${BOOTSTRAP_AGE_PUBLIC_KEY} -o ./clusters/lovenet/apps/media/kodidb/secrets.yaml ./clusters/lovenet/apps/media/kodidb/tmpl/secrets-tmpl.yaml
age -e -r ${BOOTSTRAP_AGE_PUBLIC_KEY} -o ./clusters/lovenet/apps/media/qbittorrent/secrets.yaml ./clusters/lovenet/apps/media/qbittorrent/tmpl/secrets-tmpl.yaml

age -e -r ${BOOTSTRAP_AGE_PUBLIC_KEY} -o ./clusters/lovenet/base/cluster-secrets.yaml ./clusters/lovenet/base/cluster-secrets.yaml
age -e -r ${BOOTSTRAP_AGE_PUBLIC_KEY} -o ./clusters/lovenet/apps/home/home-assistant/secrets.yaml ./clusters/lovenet/apps/home/home-assistant/secrets.yaml
age -e -r ${BOOTSTRAP_AGE_PUBLIC_KEY} -o ./clusters/lovenet/apps/flux-system/webhook/github/secrets.yaml ./clusters/lovenet/apps/flux-system/webhook/github/secrets.yaml
age -e -r ${BOOTSTRAP_AGE_PUBLIC_KEY} -o ./clusters/lovenet/apps/home/appdaemon/secrets.yaml ./clusters/lovenet/apps/home/appdaemon/secrets.yaml
age -e -r ${BOOTSTRAP_AGE_PUBLIC_KEY} -o ./clusters/lovenet/core/notifications/github/secrets.yaml ./clusters/lovenet/core/notifications/github/secrets.yaml
age -e -r ${BOOTSTRAP_AGE_PUBLIC_KEY} -o ./clusters/lovenet/apps/network/traefik/middlewares/secrets.yaml ./clusters/lovenet/apps/network/traefik/middlewares/secrets.yaml
age -e -r ${BOOTSTRAP_AGE_PUBLIC_KEY} -o ./clusters/lovenet/core/cert-manager/secrets.yaml ./clusters/lovenet/core/cert-manager/secrets.yaml
age -e -r ${BOOTSTRAP_AGE_PUBLIC_KEY} -o ./clusters/lovenet/apps/network/external-dns/secrets.yaml ./clusters/lovenet/apps/network/external-dns/secrets.yaml

# Frigate (new style, convert others)
envsubst < ./clusters/lovenet/apps/home/frigate/secrets-tmpl.yaml > ./clusters/lovenet/apps/home/frigate/secrets.yaml
age -e -r ${BOOTSTRAP_AGE_PUBLIC_KEY} -o ./clusters/lovenet/apps/home/frigate/secrets.yaml ./clusters/lovenet/apps/home/frigate/secrets.yaml
