#!/bin/bash

# No `set -e`: teardown must continue past individual failures (a node may
# already be down). -u and pipefail still catch unset vars and broken pipes.
set -uo pipefail

: "${SECRET_DOMAIN:?SECRET_DOMAIN must be set (export SECRET_DOMAIN=<your-cluster-domain>)}"

# Canonical node lists (DRAIN_ORDER, CEPH_CLEANUP_NODES).
source "$(dirname "${BASH_SOURCE[0]}")/nodes.sh"

cat <<EOF
#############################################################################
#  DESTRUCTIVE: this drains every node, runs 'kubeadm reset', wipes Ceph
#  OSD devices, and clears /var/lib/{etcd,kubelet,longhorn,rook}.
#
#  Only the Garage S3 CNPG backups and NFS-backed data survive. Verify NFS
#  host health and a recent CNPG backup FIRST — see docs/src/cluster_rebuild.md
#  "Preflight".
#############################################################################
EOF
read -r -p "Type DESTROY to proceed: " confirm
[ "${confirm}" = "DESTROY" ] || { echo "Aborted."; exit 1; }

reset_cmd='kubeadm reset -f'

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

for short in "${DRAIN_ORDER[@]}" ; do
    node="${short}.${SECRET_DOMAIN}"
    echo "## $node ## kubectl drain $node --delete-emptydir-data --force --ignore-daemonsets --grace-period=0"
    kubectl drain $node --delete-emptydir-data --force --ignore-daemonsets --grace-period=0

    echo "## $node ## kubectl delete node $node"
    kubectl delete node $node
done

for short in "${DRAIN_ORDER[@]}" ; do
    node="${short}.${SECRET_DOMAIN}"
    echo "## $node ## ${reset_cmd} ##"
    ssh root@$node "$reset_cmd"

    echo "## $node ## rm -rf ~/.kube"
    ssh root@$node "rm -rf ~/.kube/"

    echo "## $node ## rm -rf /etc/cni/"
    ssh root@$node "rm -rf /etc/cni/"

    echo "## $node ## rm -rf /etc/kubernetes/"
    ssh root@$node "rm -rf /etc/kubernetes/"

    echo "## $node ## rm -rf /var/lib/kubelet/"
    ssh root@$node "rm -rf /var/lib/kubelet/"

    echo "## $node ## rm -rf /var/lib/etcd/"
    ssh root@$node "rm -rf /var/lib/etcd/"

    echo "## $node ## clear iptables"
    ssh root@$node "iptables -F && iptables -X && \
                 iptables -t nat -F && iptables -t nat -X && \
                 iptables -t raw -F && iptables -t raw -X && \
             iptables -t mangle -F && iptables -t mangle -X"
    echo "## $node ## Restart crio"
    ssh root@$node "systemctl restart crio"
done

if [ -d ${HOME}/.kube ] ; then
    echo "## rm -rf ${HOME}/.kube/*"
    rm -rf ${HOME}/.kube/*
fi

for worker in "${CEPH_CLEANUP_NODES[@]}" ; do
    node="${worker}.${SECRET_DOMAIN}"
    echo "cleaning up ${node}"
    echo "- run /root/ceph-cleanup.sh"
    ssh root@${node} /root/ceph-cleanup.sh

    # Whole pipeline must run on the node: a non-quoted 'ssh host ls … | xargs
    # dmsetup' expands the glob and runs dmsetup LOCALLY. Quote it so the
    # ls/xargs/dmsetup all execute remotely. -r skips dmsetup on an empty list.
    echo "- dmsetup remove"
    ssh root@${node} 'ls /dev/mapper/ceph-* 2>/dev/null | xargs -r -I% dmsetup remove %'

    echo "- rm -rf /dev/ceph-* /dev/mapper/ceph--*"
    ssh root@${node} 'rm -rf /dev/ceph-* /dev/mapper/ceph--*'
done

./tools/run-on-all-nodes.sh rm -rf /var/lib/rook/*
