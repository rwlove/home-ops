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

ssh root@worker2 /root/ceph-cleanup.sh
ssh root@worker5 /root/ceph-cleanup.sh
ssh root@worker6 /root/ceph-cleanup.sh
ssh root@worker9 /root/ceph-cleanup.sh

ssh root@worker2 ls /dev/mapper/ceph-* | xargs -I% -- dmsetup remove %
ssh root@worker5 ls /dev/mapper/ceph-* | xargs -I% -- dmsetup remove %
ssh root@worker6 ls /dev/mapper/ceph-* | xargs -I% -- dmsetup remove %
ssh root@worker9 ls /dev/mapper/ceph-* | xargs -I% -- dmsetup remove %

ssh root@worker2 rm -rf /dev/ceph-* /dev/mapper/ceph--*
ssh root@worker5 rm -rf /dev/ceph-* /dev/mapper/ceph--*
ssh root@worker6 rm -rf /dev/ceph-* /dev/mapper/ceph--*
ssh root@worker9 rm -rf /dev/ceph-* /dev/mapper/ceph--*
