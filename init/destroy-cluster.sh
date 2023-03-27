#!/bin/bash


reset_cmd='kubeadm reset -f'

for node in worker1.thesteamedcrab.com \
        worker2.thesteamedcrab.com \
        worker3.thesteamedcrab.com \
        worker4.thesteamedcrab.com \
        worker5.thesteamedcrab.com \
        worker6.thesteamedcrab.com \
        worker7.thesteamedcrab.com \
        worker8.thesteamedcrab.com \
        worker9.thesteamedcrab.com \
	worker10.thesteamedcrab.com \
        master3.thesteamedcrab.com \
        master2.thesteamedcrab.com \
        master1.thesteamedcrab.com \
          ; do
    #echo "#### Drain $worker ####"
    #kubectl drain $node --delete-emptydir-data --force --ignore-daemonsets

    echo "#### Delete $node ####"
    kubectl delete node $node
done

if [ -d ${HOME}/.kube ] ; then
    echo "#### Delete ${HOME}/.kube since it exists ####"
    rm -rf ${HOME}/.kube
fi

for node in worker1.thesteamedcrab.com \
          worker2.thesteamedcrab.com \
          worker3.thesteamedcrab.com \
          worker4.thesteamedcrab.com \
          worker5.thesteamedcrab.com \
          worker6.thesteamedcrab.com \
          worker7.thesteamedcrab.com \
          worker8.thesteamedcrab.com \
          worker9.thesteamedcrab.com \
	  worker10.thesteamedcrab.com \
          master3.thesteamedcrab.com \
          master2.thesteamedcrab.com \
          master1.thesteamedcrab.com \
          ; do
    echo "#### Reset $node ####"
    ssh $node "$reset_cmd"
    echo "#### rm -rf ~/.kube, /etc/cni, /etc/kubernetes, /var/lib/kubelet, /var/lib/etcd on $node ####"
    ssh $node "rm -rf ~/.kube/ /etc/cni /etc/kubernetes/ /var/lib/kubelet/ /var/lib/etcd/"

    echo "#### Clear iptables $node ####"
    ssh $node "iptables -F && iptables -X && \
                 iptables -t nat -F && iptables -t nat -X && \
                 iptables -t raw -F && iptables -t raw -X && \
             iptables -t mangle -F && iptables -t mangle -X"
    echo "#### Restart crio $node ####"
    ssh $node "systemctl restart crio"
done
