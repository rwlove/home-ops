#!/bin/bash

#. ./env_vars.txt
. .cluster-secrets.env

flux bootstrap github --verbose --owner=$GITHUB_USER --repository=fleet-infra --branch=main --path=./clusters/lovenet/base --personal --log-level debug
