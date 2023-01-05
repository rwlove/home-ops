#!/bin/bash


kubectl -n home scale --replicas=1 deploy/frigate
kubectl -n home scale --replicas=1 deploy/hajimari

kubectl -n media scale --replicas=1 deploy/gonic
kubectl -n media scale --replicas=1 deploy/kodi-helm-kodi-matrix
kubectl -n media scale --replicas=1 deploy/rompr
kubectl -n media scale --replicas=1 deploy/stash
kubectl -n media scale --replicas=1 deploy/youtubedl-material

kubectl -n monitoring scale --replicas=1 deploy/statping deploy/portainer deploy/netdata-parent

kubectl -n collab scale --replicas=1 deploy/vikunja

# Stateful Sets
kubectl -n media scale --replicas=1 statefulsets.apps kodidb-helm-mariadb whisparr lidarr sonarr radarr airsonic
kubectl -n home scale --replicas=1 statefulsets.apps home-assistant emqx zwavejs2mqtt-z-stick-7 zwavejs2mqtt node-red
kubectl -n downloads scale --replicas=1 statefulsets.apps prowlarr
kubectl -n monitoring scale --replicas=1 statefulsets.apps statping statping-postgresql
kubectl -n databases scale --replicas=1 statefulsets.apps redis-master redis-replicas influxdb-influxdb2
