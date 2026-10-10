#!/bin/bash

set -eu -o pipefail

: "${SECRET_DOMAIN:?SECRET_DOMAIN must be set (export SECRET_DOMAIN=<your-cluster-domain>)}"

# Canonical node lists (MASTER_INIT, JOIN_MASTERS, WORKERS, LONGHORN_NODES).
source "$(dirname "${BASH_SOURCE[0]}")/nodes.sh"

# Load br_netfilter and enable IPv4 forwarding on an ssh target. kubeadm
# join needs both; nodes may not have them set before first boot into the
# cluster. Uses the default ssh user (matches the join calls below).
prep_netfilter() {
    ssh "$1" "modprobe br_netfilter; echo '1' > /proc/sys/net/ipv4/ip_forward"
}

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
# set -e aborts on a failed kubeadm init, so no explicit exit-code check.
kubeadm init --skip-phases=addon/kube-proxy,addon/coredns --config "$rendered_config"

echo "#### Copy K8S config ####"
mkdir -p ${HOME}/.kube
cp -f /etc/kubernetes/admin.conf ${HOME}/.kube/config
chown -R ${USER}:${USER} ${HOME}/.kube

certs=`kubeadm init phase upload-certs --upload-certs --config "$rendered_config" | tail -n 1`
echo "certs: ${certs}"
worker_join_cmd=`kubeadm token create --print-join-command`
master_join_cmd="${worker_join_cmd} --control-plane --certificate-key ${certs}"

for cp_host in "${JOIN_MASTERS[@]}" ; do
    control_plane="${cp_host}.${SECRET_DOMAIN}"
    echo "########## Joining (master) $control_plane to the Cluster #"
    prep_netfilter "$control_plane"
    ssh "$control_plane" "$master_join_cmd"
    ssh "$control_plane" "mkdir -p /etc/kubernetes/manifests"
done

for worker_host in "${WORKERS[@]}" ; do
    worker="${worker_host}.${SECRET_DOMAIN}"
    echo "$worker netfilter setup"
    prep_netfilter "$worker"
    echo "########## Joining (worker) $worker to the Cluster #"
    ssh "$worker" "$worker_join_cmd"
    ssh "$worker" "mkdir -p /etc/kubernetes/manifests"
done

# Configure Longhorn Disks (NVMe Drives) -- see README hardware section.
# worker8 is intentionally excluded (diskless in Longhorn — see nodes.sh).
echo "Label Longhorn nodes (${LONGHORN_NODES[*]}) since they have NVMe drives"
for longhorn_host in "${LONGHORN_NODES[@]}" ; do
    node="${longhorn_host}.${SECRET_DOMAIN}"
    ssh root@${node} rm -rf /var/lib/longhorn/*
    kubectl label nodes ${node} "node.longhorn.io/create-default-disk=true"
done

echo "Make master1 schedulable"
kubectl taint nodes ${MASTER_INIT}.${SECRET_DOMAIN} node-role.kubernetes.io/control-plane:NoSchedule-
