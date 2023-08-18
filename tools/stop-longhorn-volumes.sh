#!/bin/bash


# Deployments
kubectl -n home scale --replicas=0 deployment hajimari double-take nextcloud deepstack
kubectl -n media scale --replicas=0 deployment gonic youtubedl-material
kubectl -n downloads scale --replicas=0 deploy/qbittorrent
kubectl -n monitoring scale --replicas=0 deployment netdata-parent
kubectl -n collab scale --replicas=0 deployment vikunja

# Stateful Sets
kubectl -n media scale --replicas=0 statefulsets lidarr sonarr radarr jellyfin sabnzbd
kubectl -n home scale --replicas=0 statefulsets home-assistant emqx zwavejs2mqtt-z-stick-7 zwavejs2mqtt node-red esphome vaultwarden frigate zigbee2mqtt
kubectl -n downloads scale --replicas=0 statefulsets prowlarr
kubectl -n radio scale --replicas=0 statefulsets mopidy
kubectl -n databases scale --replicas=0 statefulsets redis-master redis-replicas influxdb-influxdb2
