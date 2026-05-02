#!/usr/bin/env bash
# Destroy a CNPG replica instance: deletes the PV, PVC, and pod for a single
# postgres-* instance. CNPG will then rebuild that replica from the primary.
#
# DO NOT run this against a primary — it will cause data loss. The script
# checks the cnpg.io/instanceRole label and refuses unless --force is given.
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <pod-name> [--force]" >&2
  echo "   eg: $0 postgres-immich-3" >&2
  exit 1
fi

pod="$1"
force="${2:-}"

role=$(kubectl -n databases get pod "${pod}" -o jsonpath='{.metadata.labels.cnpg\.io/instanceRole}' 2>/dev/null || true)
if [ "${role}" = "primary" ] && [ "${force}" != "--force" ]; then
  echo "ERROR: ${pod} is the PRIMARY. Refusing to destroy."
  echo "If you really mean it, re-run with --force (you'll lose committed data not yet replicated)."
  exit 2
fi

pv=$(kubectl -n databases get pvc "${pod}" -o jsonpath='{.spec.volumeName}')
[ -n "${pv}" ] || { echo "could not find PV for PVC ${pod}"; exit 3; }

echo "About to destroy CNPG replica ${pod}:"
echo "  pod : ${pod}"
echo "  pvc : ${pod}"
echo "  pv  : ${pv}"
echo "  role: ${role:-unknown}"
read -r -p "Type the pod name to confirm: " confirm
[ "${confirm}" = "${pod}" ] || { echo "aborted"; exit 1; }

kubectl delete pv "${pv}" &
kubectl -n databases delete pvc "${pod}" &
kubectl -n databases delete pod "${pod}"
wait
