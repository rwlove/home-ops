#!/bin/bash

if [ -d ${HOME}/.kube ] ; then
    echo "#### Delete ${HOME}/.kube/* since it exists ####"
    rm -rf ${HOME}/.kube/*
fi

echo "Create Kube VIP"
./init/kube-vip.sh

modprobe br_netfilter
echo '1' > /proc/sys/net/ipv4/ip_forward

echo "#### Initialize the K8S Cluster ####"
kubeadm init --skip-phases=addon/kube-proxy --config ./init/clusterconfiguration.yaml
[ $? -ne 0 ] && exit 1

echo "#### Copy K8S config ####"
mkdir ${HOME}/.kube
cp -f /etc/kubernetes/admin.conf ${HOME}/.kube/config
chown -R ${USER}.${USER} ${HOME}/.kube

certs=`kubeadm init phase upload-certs --upload-certs --config ./init/clusterconfiguration.yaml | tail -n 1`
echo "certs: ${certs}"
worker_join_cmd=`kubeadm token create --print-join-command`
master_join_cmd="${worker_join_cmd} --control-plane --certificate-key ${certs}"

#echo "XXXXXXXXXXX master_join_cmd START XXXXXXXXXXX"
#echo "${master_join_cmd}"
#echo "XXXXXXXXXXX master_join_cmd END XXXXXXXXXXX"

for control_plane in master2.thesteamedcrab.com master3.thesteamedcrab.com ; do
    echo "########## Joining (master) $control_plane to the Cluster #"
    ssh "$control_plane" modprobe br_netfilter
    echo_cmd="echo '1' > /proc/sys/net/ipv4/ip_forward"
    ssh "$control_plane"  "$echo_cmd"
    ssh "$control_plane" "$master_join_cmd"
    ssh "$control_plane" "mkdir /etc/kubernetes/manifests"
done

for worker in worker1.thesteamedcrab.com \
		  worker2.thesteamedcrab.com \
		  worker3.thesteamedcrab.com \
		  worker4.thesteamedcrab.com \
		  worker5.thesteamedcrab.com \
		  worker6.thesteamedcrab.com \
		  worker7.thesteamedcrab.com \
		  worker8.thesteamedcrab.com \
          ; do
    echo "$worker netfilter setup"
    ssh "$control_plane" modprobe br_netfilter
    echo_cmd="echo '1' > /proc/sys/net/ipv4/ip_forward"
    ssh "$control_plane"  "$echo_cmd"
    echo "########## Joining (worker) $worker to the Cluster #"
    ssh "$worker" "$worker_join_cmd"
    echo "mkdir /etc/kubernetes/manifests"
    ssh "$worker" "mkdir /etc/kubernetes/manifests"
done

# Configure Longhorn Disks (NVMe Drives) -- see README hardware section
echo " Label workers 1, 2, 3, 4, 5, 6, 7 and 8 for longhorn since they have NVMe drives"
ssh root@worker1.thesteamedcrab.com rm -rf /var/lib/longhorn/*
kubectl label nodes worker1.thesteamedcrab.com "node.longhorn.io/create-default-disk=true"
ssh root@worker2.thesteamedcrab.com rm -rf /var/lib/longhorn/*
kubectl label nodes worker2.thesteamedcrab.com "node.longhorn.io/create-default-disk=true"
ssh root@worker3.thesteamedcrab.com rm -rf /var/lib/longhorn/*
kubectl label nodes worker3.thesteamedcrab.com "node.longhorn.io/create-default-disk=true"
ssh root@worker4.thesteamedcrab.com rm -rf /var/lib/longhorn/*
kubectl label nodes worker4.thesteamedcrab.com "node.longhorn.io/create-default-disk=true"
ssh root@worker5.thesteamedcrab.com rm -rf /var/lib/longhorn/*
kubectl label nodes worker5.thesteamedcrab.com "node.longhorn.io/create-default-disk=true"
#ssh root@worker6.thesteamedcrab.com rm -rf /var/lib/longhorn/*
#kubectl label nodes worker6.thesteamedcrab.com "node.longhorn.io/create-default-disk=true"
ssh root@worker7.thesteamedcrab.com rm -rf /var/lib/longhorn/*
kubectl label nodes worker7.thesteamedcrab.com "node.longhorn.io/create-default-disk=true"
ssh root@worker8.thesteamedcrab.com rm -rf /var/lib/longhorn/*
kubectl label nodes worker8.thesteamedcrab.com "node.longhorn.io/create-default-disk=true"
