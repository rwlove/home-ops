#!/bin/bash

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

for worker in worker5 worker6 worker7 worker8 ; do
    echo "cleaning up ${worker}"
    echo "- run /root/ceph-cleanup.sh"
    ssh root@${worker} /root/ceph-cleanup.sh

    echo "- dmsetup remove"
    ssh root@${worker} ls /dev/mapper/ceph-* | xargs -I% -- dmsetup remove %

    echo "- rm -rf /dev/ceph-* /dev/mapper/ceph--*"
    ssh root@${worker} rm -rf /dev/ceph-* /dev/mapper/ceph--*
done

./tools/run-on-all-nodes.sh rm -rf /var/lib/rook/*
