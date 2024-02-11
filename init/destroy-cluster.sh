#!/bin/bash

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

for node in worker1.thesteamedcrab.com \
	worker2.thesteamedcrab.com \
	worker3.thesteamedcrab.com \
        worker4.thesteamedcrab.com \
        worker5.thesteamedcrab.com \
        worker6.thesteamedcrab.com \
        worker7.thesteamedcrab.com \
        worker8.thesteamedcrab.com \
        master3.thesteamedcrab.com \
        master2.thesteamedcrab.com \
        master1.thesteamedcrab.com \
          ; do
    echo "## $node ## kubectl drain $node --delete-emptydir-data --force --ignore-daemonsets --grace-period=0"
    kubectl drain $node --delete-emptydir-data --force --ignore-daemonsets --grace-period=0

    echo "## $node ## kubectl delete node $node"
    kubectl delete node $node
done

for node in worker1.thesteamedcrab.com \
	  worker2.thesteamedcrab.com \
	  worker3.thesteamedcrab.com \
          worker4.thesteamedcrab.com \
          worker5.thesteamedcrab.com \
          worker6.thesteamedcrab.com \
          worker7.thesteamedcrab.com \
          worker8.thesteamedcrab.com \
          master3.thesteamedcrab.com \
          master2.thesteamedcrab.com \
          master1.thesteamedcrab.com \
          ; do
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

for worker in worker3 worker5 worker6 worker7 worker8 ; do
    echo "cleaning up ${worker}"
    echo "- run /root/ceph-cleanup.sh"
    ssh root@${worker} /root/ceph-cleanup.sh

    echo "- dmsetup remove"
    ssh root@${worker} ls /dev/mapper/ceph-* | xargs -I% -- dmsetup remove %

    echo "- rm -rf /dev/ceph-* /dev/mapper/ceph--*"
    ssh root@${worker} rm -rf /dev/ceph-* /dev/mapper/ceph--*
done

./tools/run-on-all-nodes.sh rm -rf /var/lib/rook/*
