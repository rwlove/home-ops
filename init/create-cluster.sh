#!/bin/bash

: "${SECRET_DOMAIN:?SECRET_DOMAIN must be set (export SECRET_DOMAIN=<your-cluster-domain>)}"

if [ -d ${HOME}/.kube ] ; then
    echo "#### Delete ${HOME}/.kube/* since it exists ####"
    rm -rf ${HOME}/.kube/*
fi

echo "Create Kube VIP"
./init/kube-vip.sh

modprobe br_netfilter
echo '1' > /proc/sys/net/ipv4/ip_forward

# clusterconfiguration.yaml references master1.${SECRET_DOMAIN}; substitute
# at invocation time since kubeadm doesn't expand env vars itself.
rendered_config=$(mktemp -t clusterconfiguration.XXXXXX.yaml)
trap 'rm -f "$rendered_config"' EXIT
envsubst < ./init/clusterconfiguration.yaml > "$rendered_config"

echo "#### Initialize the K8S Cluster ####"
kubeadm init --skip-phases=addon/kube-proxy,addon/coredns --config "$rendered_config"
[ $? -ne 0 ] && exit 1

echo "#### Copy K8S config ####"
mkdir ${HOME}/.kube
cp -f /etc/kubernetes/admin.conf ${HOME}/.kube/config
chown -R ${USER}.${USER} ${HOME}/.kube

certs=`kubeadm init phase upload-certs --upload-certs --config "$rendered_config" | tail -n 1`
echo "certs: ${certs}"
worker_join_cmd=`kubeadm token create --print-join-command`
master_join_cmd="${worker_join_cmd} --control-plane --certificate-key ${certs}"

#echo "XXXXXXXXXXX master_join_cmd START XXXXXXXXXXX"
#echo "${master_join_cmd}"
#echo "XXXXXXXXXXX master_join_cmd END XXXXXXXXXXX"

for cp_host in master2 master3 ; do
    control_plane="${cp_host}.${SECRET_DOMAIN}"
    echo "########## Joining (master) $control_plane to the Cluster #"
    ssh "$control_plane" modprobe br_netfilter
    echo_cmd="echo '1' > /proc/sys/net/ipv4/ip_forward"
    ssh "$control_plane"  "$echo_cmd"
    ssh "$control_plane" "$master_join_cmd"
    ssh "$control_plane" "mkdir /etc/kubernetes/manifests"
done

for worker_host in worker2 worker3 worker4 worker5 worker6 worker7 worker8 ; do
    worker="${worker_host}.${SECRET_DOMAIN}"
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
for longhorn_host in master1 worker2 worker3 worker4 worker5 worker6 worker7 ; do
    node="${longhorn_host}.${SECRET_DOMAIN}"
    ssh root@${node} rm -rf /var/lib/longhorn/*
    kubectl label nodes ${node} "node.longhorn.io/create-default-disk=true"
done
#ssh root@worker8.${SECRET_DOMAIN} rm -rf /var/lib/longhorn/*
#kubectl label nodes worker8.${SECRET_DOMAIN} "node.longhorn.io/create-default-disk=true"

echo "Make master1 schedulable"
kubectl taint nodes master1.${SECRET_DOMAIN} node-role.kubernetes.io/control-plane:NoSchedule-
