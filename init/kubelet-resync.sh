#!/bin/bash
#
# Re-sync one node's kubelet config from the kube-system/kubelet-config
# ConfigMap plus that node's patches from git, then restart the kubelet.
# Running containers and static pods keep running. Prefer
# `just cluster kubelet-resync <node>`. Run from the repo root on the laptop.
set -eu -o pipefail

: "${SECRET_DOMAIN:?SECRET_DOMAIN must be set (export SECRET_DOMAIN=<your-cluster-domain>)}"
node_short=${1:?usage: kubelet-resync.sh <short-node-name>}
source "$(dirname "${BASH_SOURCE[0]}")/nodes.sh"
node="${node_short}.${SECRET_DOMAIN}"

sync_kubelet_patches "$node_short"
scp -q ./init/upgradeconfiguration.yaml "root@${node}:/etc/kubernetes/upgradeconfiguration.yaml"
ssh "root@${node}" "cp /var/lib/kubelet/config.yaml /var/lib/kubelet/config.yaml.bak && \
  kubeadm upgrade node phase kubelet-config --config /etc/kubernetes/upgradeconfiguration.yaml && \
  systemctl restart kubelet"

# Node Ready lags the restart (it still reads True from before), so wait for
# the new kubelet to answer configz, then for Ready.
cfg=""
for _ in $(seq 1 24); do
    cfg=$(kubectl get --raw "/api/v1/nodes/${node}/proxy/configz" 2>/dev/null) && break
    sleep 5
done
[ -n "$cfg" ] || { echo "${node_short}: kubelet did not come back" >&2; exit 1; }
kubectl wait --for=condition=Ready "node/${node}" --timeout=120s
jq -c '.kubeletconfig | {systemReserved, kubeReserved, evictionHard}' <<<"$cfg"
