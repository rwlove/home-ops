#!/bin/bash

kubectl -n media exec -i -t `kubectl -n media get pods -l app.kubernetes.io/instance=kodi-nexus | grep kodi-nexus | cut -d ' ' -f 1` -- tail -F /config/.kodi/temp/kodi.log
