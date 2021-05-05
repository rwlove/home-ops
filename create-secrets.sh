#!/bin/bash

export GPG_TTY=$(tty)

. .cluster-secrets.env

envsubst < ./tmpl/.sops.yaml > ./.sops.yaml
envsubst < ./tmpl/cluster-secrets.yaml > ./clusters/lovenet/base/cluster-secrets.yaml
envsubst < ./tmpl/cluster-settings.yaml > ./clusters/lovenet/base/cluster-settings.yaml
envsubst < ./tmpl/gotk-sync.yaml > ./clusters/lovenet/base/flux-system/gotk-sync.yaml

sops --encrypt --in-place ./clusters/lovenet/base/cluster-secrets.yaml

sops --encrypt ./clusters/lovenet/core/monitoring/grafana/tmpl/helm-values.yaml > ./clusters/lovenet/core/monitoring/grafana/grafana-helm-values.yaml
sops --encrypt ./clusters/lovenet/core/monitoring/prometheus/tmpl/prometheus-helm-values.yaml > ./clusters/lovenet/core/monitoring/prometheus/prometheus-helm-values.yaml
