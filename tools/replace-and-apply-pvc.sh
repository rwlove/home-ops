#!/bin/bash

# $1 = path-to-file

export SECRET_NFS_HOST_0="brain.thesteamedcrab.com"
export SECRET_NFS_HOST_2="beast.thesteamedcrab.com"

envsubst < ./$1 > ./$1-hardcoded

kubectl apply -f ./$1-hardcoded
