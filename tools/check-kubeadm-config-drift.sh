#!/bin/bash
# Advisory drift check: compare the bootstrap-only
# init/clusterconfiguration.yaml (what a rebuild would install) against the
# live kube-system/kubeadm-config ConfigMap (what the cluster actually runs).
#
# This file is NOT Flux-reconciled, so it is hand-maintained to mirror live
# state. Divergence means a rebuild would install stale control-plane config
# (this has bitten dns/proxy before). Run it periodically and before any
# rebuild. Read-only; exits non-zero on drift so it can gate a check.
#
# Note: kubeadm injects fields the repo file omits (certSANs, featureGates,
# etc.), so some diff lines are expected — read the diff, don't just trust
# the exit code.
set -euo pipefail

: "${SECRET_DOMAIN:?SECRET_DOMAIN must be set (export SECRET_DOMAIN=<your-cluster-domain>)}"

here="$(dirname "${BASH_SOURCE[0]}")"
src="${here}/../init/clusterconfiguration.yaml"

rendered="$(mktemp)"; live="$(mktemp)"
trap 'rm -f "$rendered" "$live"' EXIT

# ClusterConfiguration doc from the repo file, env-substituted and key-sorted.
envsubst < "$src" \
  | yq eval-all 'select(.kind == "ClusterConfiguration")' - \
  | yq -P 'sort_keys(..)' - > "$rendered"

# Live ClusterConfiguration from the kubeadm-config ConfigMap, key-sorted.
kubectl -n kube-system get configmap kubeadm-config \
  -o jsonpath='{.data.ClusterConfiguration}' \
  | yq -P 'sort_keys(..)' - > "$live"

if diff -u "$live" "$rendered" > /dev/null; then
  echo "OK: init/clusterconfiguration.yaml matches live kubeadm-config ClusterConfiguration."
  exit 0
fi

echo "DRIFT: repo (+, right) vs live kubeadm-config (-, left):"
echo
diff -u "$live" "$rendered" || true
echo
echo "A rebuild installs the repo file. Reconcile the drift before destroying anything."
exit 1
