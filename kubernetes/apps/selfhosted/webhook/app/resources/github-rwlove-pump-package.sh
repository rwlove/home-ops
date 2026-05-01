#!/usr/bin/env bash
set -euo pipefail

# Triggers the rwlove-pump RenovateJob, which is scoped to
# kubernetes/apps/collab/pump/app/helmrelease.yaml only.
JOB="rwlove-pump"
NAMESPACE="renovate"
PROJECT="rwlove/home-ops"
RENOVATE_OPERATOR_WEBHOOK_URL="http://renovate-operator.renovate.svc.cluster.local:8082"

# URL encode the project name
PROJECT=$(echo "${PROJECT}" | jq -Rr @uri)

curl -v -X POST \
  "${RENOVATE_OPERATOR_WEBHOOK_URL}/webhook/v1/schedule?job=${JOB}&namespace=${NAMESPACE}&project=${PROJECT}"
