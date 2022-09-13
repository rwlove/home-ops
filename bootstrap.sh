#!/bin/bash

#. ./env_vars.txt
. .cluster-secrets.env

echo "GITHUB_USER: ${GITHUB_USER}"
echo "GITHUB_TOKEN: ${GITHUB_TOKEN}"

flux bootstrap github --verbose --owner=$GITHUB_USER --repository=fleet-infra --branch=main --path=./clusters/lovenet/base --personal --log-level debug
