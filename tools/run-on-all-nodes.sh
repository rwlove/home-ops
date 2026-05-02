#!/usr/bin/env bash
# Run the given command via ssh root@<node> on every node in the cluster.
# Node list is discovered dynamically from kubectl.
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <command> [args...]" >&2
  exit 1
fi

nodes=$(kubectl get nodes -o jsonpath='{.items[*].metadata.name}')

for node in ${nodes}; do
  echo "## node ${node}"
  ssh "root@${node}" "$@"
done
