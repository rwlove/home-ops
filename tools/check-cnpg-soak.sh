#!/usr/bin/env bash
# Soak check for CNPG postgres clusters after resource-spec rollout.
# Run this 24h+ after commit 545fe46deb to verify the sizing held.
set -euo pipefail

ns=databases
since="${SINCE:-24h}"
echo "## Soak check (looking back $since)"
echo

echo "### Restart counts (any > 0 means a pod was killed and restarted)"
kubectl get pods -n "$ns" -l 'cnpg.io/cluster' -o json \
  | jq -r '.items[] | "\(.metadata.name)\t\(.status.containerStatuses[]?.restartCount)"' \
  | awk -F'\t' '$2 > 0 { print }'
echo "  (empty = good)"
echo

echo "### OOMKilled events (databases ns, last $since)"
kubectl get events -n "$ns" --field-selector reason=OOMKilling 2>/dev/null \
  | tail -n +2
echo "  (empty = good)"
echo

echo "### Recent terminated containers (any reason, last $since)"
kubectl get pods -n "$ns" -l 'cnpg.io/cluster' -o json \
  | jq -r '.items[]
            | select(.status.containerStatuses[]?.lastState.terminated != null)
            | "\(.metadata.name)\t\(.status.containerStatuses[].lastState.terminated.reason)\t\(.status.containerStatuses[].lastState.terminated.finishedAt)"'
echo "  (empty = good)"
echo

echo "### Current peak memory vs request (anything >70% needs attention)"
declare -A REQ=(
  [postgres-atuin]=1024 [postgres-cutvideo]=512 [postgres-home-assistant]=1536
  [postgres-immich]=1024 [postgres-lldap]=512 [postgres-medikeep]=1024
  [postgres-nametag]=512 [postgres-netbox]=512 [postgres-nextcloud]=512
  [postgres-paperless]=512 [postgres-romm]=1024
  [postgres-sparkyfitness]=1024
  [postgres-workoutdiary]=1024
)
kubectl top pods -n "$ns" --no-headers 2>/dev/null \
  | awk '/^postgres-/ { print $1, $3 }' \
  | while read pod mem; do
      cluster="${pod%-*}"
      req="${REQ[$cluster]:-512}"
      # parse Mi suffix
      used="${mem%Mi}"
      pct=$(( used * 100 / req ))
      if [ "$pct" -gt 70 ]; then
        printf "  ⚠ %-30s %5sMi / %sMi (%d%%)\n" "$pod" "$used" "$req" "$pct"
      fi
    done
echo "  (empty = good — all clusters under 70% of request)"
echo

echo "### Cluster phase summary"
kubectl get cluster.postgresql.cnpg.io -n "$ns" \
  -o custom-columns=NAME:.metadata.name,INSTANCES:.spec.instances,READY:.status.readyInstances,STATUS:.status.phase
