#!/usr/bin/env bash
# Destroy the Rook-Ceph cluster: suspends Flux, removes finalizers, deletes
# CephCluster + storageclasses, runs node-side cleanup, and wipes /var/lib/rook
# everywhere. Catastrophic and irreversible — confirms before proceeding.
set -euo pipefail

cat <<'EOF'
================================================================================
WARNING: This will DESTROY your Rook-Ceph cluster and all data on it.
- Suspends Flux for rook-ceph-cluster + rook-ceph-operator HRs
- Removes finalizers and deletes the CephCluster
- Deletes ceph-block / ceph-bucket / ceph-filesystem storageclasses
- SSHes to every node hosting an OSD and runs ceph-cleanup
- rm -rf /var/lib/rook on every node
================================================================================
EOF
read -r -p "Type 'DESTROY' to proceed: " confirm
[ "${confirm}" = "DESTROY" ] || { echo "aborted"; exit 1; }

flux -n rook-ceph suspend hr rook-ceph-cluster
flux -n rook-ceph suspend hr rook-ceph-operator

kubectl patch cephblockpools.ceph.rook.io ceph-blockpool -n rook-ceph -p '{"metadata":{"finalizers":[]}}' --type=merge
kubectl patch cephclusters.ceph.rook.io rook-ceph -n rook-ceph -p '{"metadata":{"finalizers":[]}}' --type=merge

kubectl delete -n rook-ceph cephblockpool ceph-blockpool

kubectl delete storageclasses.storage.k8s.io ceph-block ceph-bucket ceph-filesystem

kubectl -n rook-ceph delete cephcluster rook-ceph

kubectl -n rook-ceph wait --for=delete cephcluster rook-ceph

kubectl -n rook-ceph delete hr rook-ceph-cluster
kubectl -n rook-ceph delete hr rook-ceph-operator

# Discover OSD-hosting nodes dynamically (they're the ones we need to wipe).
osd_nodes=$(kubectl -n rook-ceph get pods -l app=rook-ceph-osd \
  -o jsonpath='{.items[*].spec.nodeName}' | tr ' ' '\n' | sort -u)

if [ -z "${osd_nodes}" ]; then
  echo "No OSD pods found — falling back to every node with rook-ceph state"
  osd_nodes=$(kubectl get nodes -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n')
fi

for node in ${osd_nodes}; do
  echo "cleaning up ${node}"
  echo "- run /root/ceph-cleanup.sh"
  ssh "root@${node}" /root/ceph-cleanup.sh

  echo "- dmsetup remove ceph mappings"
  ssh "root@${node}" 'ls /dev/mapper/ceph-* 2>/dev/null | xargs -r -I% dmsetup remove %'

  echo "- rm -rf /dev/ceph-* /dev/mapper/ceph--*"
  ssh "root@${node}" 'rm -rf /dev/ceph-* /dev/mapper/ceph--*'
done

# Wipe rook state on every node.
"$(dirname "$0")/run-on-all-nodes.sh" rm -rf /var/lib/rook/*
