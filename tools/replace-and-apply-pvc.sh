#!/bin/bash

# $1 = path-to-file

: "${SECRET_DOMAIN:?SECRET_DOMAIN must be set}"

export SECRET_NFS_HOST_0="brain.${SECRET_DOMAIN}"
export SECRET_NFS_HOST_2="beast.${SECRET_DOMAIN}"
export SECRET_NFS_HOST_SECURITY="security-storage.${SECRET_DOMAIN}"

envsubst < ./$1 > ./$1-hardcoded

kubectl apply -f ./$1-hardcoded
