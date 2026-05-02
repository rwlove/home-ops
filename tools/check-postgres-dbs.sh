#!/usr/bin/env bash
# Print Status / Instances / Ready instances for every CNPG cluster in the
# databases namespace.
set -euo pipefail

dbs=$(kubectl get cluster.postgresql.cnpg.io -n databases -o jsonpath='{.items[*].metadata.name}')

for db in ${dbs}; do
  echo "Database: ${db}"
  kubectl cnpg -n databases status "${db}" | grep -E "^(Status|Instances|Ready instances):"
done
