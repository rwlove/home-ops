#!/usr/bin/env bash
set -euo pipefail

# Triggers the rwlove-pump RenovateJob, which is scoped to the three PUMP
# HelmReleases (pump, pump-cv, pump-voltra).
#
# Fired by the github-rwlove-pump-package hook on a `release: published`
# event from rwlove/PUMP. That repo now creates a GitHub Release
# automatically once an image is in GHCR (.github/workflows/release.yml);
# before 2026-08-08 releases were made by hand, the habit lapsed on
# 2026-07-03, and this chain went silent — every deploy since was manual.
JOB="rwlove-pump"
NAMESPACE="renovate"
PROJECT="rwlove/home-ops"
RENOVATE_OPERATOR_WEBHOOK_URL="http://renovate-operator.renovate.svc.cluster.local:8082"

# URL encode the project name
PROJECT=$(echo "${PROJECT}" | jq -Rr @uri)

curl -v -X POST \
  "${RENOVATE_OPERATOR_WEBHOOK_URL}/webhook/v1/schedule?job=${JOB}&namespace=${NAMESPACE}&project=${PROJECT}"
