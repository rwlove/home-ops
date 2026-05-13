#!/bin/bash

: "${SECRET_DOMAIN:?SECRET_DOMAIN must be set}"

curl -L jellyfin-internal.${SECRET_DOMAIN}:8096
