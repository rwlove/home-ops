#!/bin/bash


kubectl -n home scale --replicas=0 deploy/frigate
kubectl -n home scale --replicas=0 deploy/hajimari
kubectl -n home scale --replicas=0 deploy/double-take

kubectl -n media scale --replicas=0 deploy/gonic
kubectl -n media scale --replicas=0 deploy/rompr
kubectl -n media scale --replicas=0 deploy/stash
kubectl -n media scale --replicas=0 deploy/youtubedl-material
kubectl -n media scale --replicas=0 deploy/photoprism

kubectl -n monitoring scale --replicas=0 deploy/statping deploy/portainer deploy/netdata-parent

kubectl -n collab scale --replicas=0 deploy/vikunja

# Stateful Sets
kubectl -n media scale --replicas=0 statefulsets.apps whisparr lidarr sonarr radarr airsonic kodi-nexus kodidb-nexus-mariadb
kubectl -n home scale --replicas=0 statefulsets.apps home-assistant emqx zwavejs2mqtt-z-stick-7 zwavejs2mqtt node-red esphome barcode-buddy grocy vaultwarden
kubectl -n downloads scale --replicas=0 statefulsets.apps prowlarr
kubectl -n databases scale --replicas=0 statefulsets.apps redis-master redis-replicas influxdb-influxdb2
