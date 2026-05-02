#!/usr/bin/env bash
# List Deployments / ReplicaSets / StatefulSets where ready < desired
# (i.e. workloads that aren't fully scheduled and running).
set -euo pipefail

echo "## Deployments (ready < desired)"
kubectl get deploy -A -o json | jq -r '
  .items[]
  | select((.status.readyReplicas // 0) < (.spec.replicas // 0))
  | "\(.metadata.namespace)/\(.metadata.name)\t\(.status.readyReplicas // 0)/\(.spec.replicas)"'

echo
echo "## ReplicaSets (ready < desired, excluding RS that own deployment scale-down)"
kubectl get rs -A -o json | jq -r '
  .items[]
  | select((.spec.replicas // 0) > 0)
  | select((.status.readyReplicas // 0) < (.spec.replicas // 0))
  | "\(.metadata.namespace)/\(.metadata.name)\t\(.status.readyReplicas // 0)/\(.spec.replicas)"'

echo
echo "## StatefulSets (ready < desired)"
kubectl get sts -A -o json | jq -r '
  .items[]
  | select((.status.readyReplicas // 0) < (.spec.replicas // 0))
  | "\(.metadata.namespace)/\(.metadata.name)\t\(.status.readyReplicas // 0)/\(.spec.replicas)"'
