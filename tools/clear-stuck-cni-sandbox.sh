#!/usr/bin/env bash
# Clear a stuck cri-o pod sandbox whose CNI teardown fails because a
# referenced CNI plugin binary is missing on the node.
#
# Symptom: kubelet logs `FailedKillPod` events repeatedly with
#   plugin type="<name>" name="<name>" failed (delete):
#   failed to find plugin "<name>" in path [/opt/cni/bin /usr/libexec/cni]
#
# Cause: an old sandbox's attached CNI config still references a
# plugin that has since been uninstalled (e.g. an istio version that
# changed from istio-cni back to istio-init initContainers). Updating
# the conflists doesn't help — the sandbox keeps its original config.
#
# Fix: drop a no-op stub binary at the expected path so CNI DEL
# succeeds, force-remove the sandbox, then remove the stub.
#
# Usage:
#   clear-stuck-cni-sandbox.sh <node> <sandbox-id> <missing-plugin>
#
# Example:
#   clear-stuck-cni-sandbox.sh worker7 65e125d1aaa60... istio-cni

set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "usage: $0 <node> <sandbox-id> <missing-plugin>" >&2
  exit 1
fi

node="$1"
sandbox="$2"
plugin="$3"

stub_path="/opt/cni/bin/${plugin}"

run() { ssh "root@${node}" "$@"; }

echo "==> Installing no-op stub for '${plugin}' on ${node}"
run "cat > ${stub_path} <<'EOF'
#!/bin/sh
exit 0
EOF
chmod +x ${stub_path}"

echo "==> Force-removing sandbox ${sandbox}"
run "crictl rmp -f ${sandbox}"

echo "==> Removing stub"
run "rm -f ${stub_path}"

echo "==> Done. Verifying:"
run "crictl pods --id ${sandbox} 2>&1 || true"
