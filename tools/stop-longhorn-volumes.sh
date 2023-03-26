#!/bin/bash


# Deployments
kubectl -n home scale --replicas=0 deployment hajimari double-take nextcloud
kubectl -n media scale --replicas=0 deployment gonic rompr stash youtubedl-material photoprism beets
kubectl -n downloads scale --replicas=0 deploy/qbittorrent
kubectl -n monitoring scale --replicas=0 deployment statping portainer netdata-parent
kubectl -n collab scale --replicas=0 deployment vikunja

# Stateful Sets
kubectl -n media scale --replicas=0 statefulsets whisparr lidarr sonarr radarr airsonic kodi-nexus kodidb-nexus-mariadb jellyfin kodi-mariadb sabnzbd
kubectl -n home scale --replicas=0 statefulsets home-assistant emqx zwavejs2mqtt-z-stick-7 zwavejs2mqtt node-red esphome barcode-buddy grocy vaultwarden frigate zigbee2mqtt
kubectl -n downloads scale --replicas=0 statefulsets prowlarr
kubectl -n databases scale --replicas=0 statefulsets redis-master redis-replicas influxdb-influxdb2
