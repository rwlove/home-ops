#!/bin/bash

export VIP=192.168.6.1
export INTERFACE=enp0s31f6

mkdir -p /etc/kubernetes/manifests/

podman run --network host --rm ghcr.io/kube-vip/kube-vip:v1.0.2 manifest pod --interface $INTERFACE --vip $VIP --vipSubnet "/32" --port 6443 --cp_namespace kube-system --controlplane --services --arp --leaderElection | tee /etc/kubernetes/manifests/kube-vip.yaml

sed -i 's#path: /etc/kubernetes/admin.conf#path: /etc/kubernetes/super-admin.conf#' /etc/kubernetes/manifests/kube-vip.yaml

#podman run --network host --rm ghcr.io/kube-vip/kube-vip:v0.7.1 manifest pod --interface $INTERFACE --vip $VIP --controlplane --services --arp --leaderElection | tee /etc/kubernetes/manifests/kube-vip.yaml
