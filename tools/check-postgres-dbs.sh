#!/bin/bash

DBS="postgres-immich postgres-home-assistant postgres-pocket-id postgres-lldap postgres-paperless postgres-atuin"

for db in $DBS ; do

    echo "Database: $db"
    kubectl cnpg -n databases status $db | grep "Status:"
    kubectl cnpg -n databases status $db | grep "Instances:"
    kubectl cnpg -n databases status $db | grep "Ready instances:"

done
