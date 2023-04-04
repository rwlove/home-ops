#!/bin/bash


reset_cmd='kubeadm reset -f'

#        worker4.thesteamedcrab.com \
for node in worker1.thesteamedcrab.com \
        worker2.thesteamedcrab.com \
        worker3.thesteamedcrab.com \
        worker5.thesteamedcrab.com \
        worker6.thesteamedcrab.com \
        worker7.thesteamedcrab.com \
        worker8.thesteamedcrab.com \
        master3.thesteamedcrab.com \
        master2.thesteamedcrab.com \
        master1.thesteamedcrab.com \
          ; do
    echo "## $node ## kubectl drain $node --delete-emptydir-data --force --ignore-daemonsets --grace-period=10"
    kubectl drain $node --delete-emptydir-data --force --ignore-daemonsets --grace-period=10

    echo "## $node ## kubectl delete node $node"
    kubectl delete node $node
done

#         worker4.thesteamedcrab.com \
for node in worker1.thesteamedcrab.com \
          worker2.thesteamedcrab.com \
          worker3.thesteamedcrab.com \
          worker5.thesteamedcrab.com \
          worker6.thesteamedcrab.com \
          worker7.thesteamedcrab.com \
          worker8.thesteamedcrab.com \
          master3.thesteamedcrab.com \
          master2.thesteamedcrab.com \
          master1.thesteamedcrab.com \
          ; do
    echo "## $node ## ${reset_cmd} ##"
    ssh $node "$reset_cmd"
    
    echo "## $node ## rm -rf ~/.kube"
    ssh $node "rm -rf ~/.kube/"

    echo "## $node ## rm -rf /etc/cni/"
    ssh $node "rm -rf /etc/cni/"

    echo "## $node ## rm -rf /etc/kubernetes/"
    ssh $node "rm -rf /etc/kubernetes/"

    echo "## $node ## /var/lib/kubelet/"
    ssh $node "rm -rf /var/lib/kubelet/"

    echo "## $node ## /var/lib/etcd/"
    ssh $node "rm -rf /var/lib/etcd/"

    echo "## $node ## clear iptables"
    ssh $node "iptables -F && iptables -X && \
                 iptables -t nat -F && iptables -t nat -X && \
                 iptables -t raw -F && iptables -t raw -X && \
             iptables -t mangle -F && iptables -t mangle -X"
    echo "## $node ## Restart crio"
    ssh $node "systemctl restart crio"
done

if [ -d ${HOME}/.kube ] ; then
    echo "## rm -rf ${HOME}/.kube"
    rm -rf ${HOME}/.kube
fi
