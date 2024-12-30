#!/bin/bash

export VIP=192.168.6.1
export INTERFACE=eno1

mkdir -p /etc/kubernetes/manifests/

podman run --network host --rm ghcr.io/kube-vip/kube-vip:v0.8.7 manifest pod --interface $INTERFACE --vip $VIP --cidr "32" --controlplane --services --arp --leaderElection | tee /etc/kubernetes/manifests/kube-vip.yaml

sed -i 's#path: /etc/kubernetes/admin.conf#path: /etc/kubernetes/super-admin.conf#' /etc/kubernetes/manifests/kube-vip.yaml

#podman run --network host --rm ghcr.io/kube-vip/kube-vip:v0.7.1 manifest pod --interface $INTERFACE --vip $VIP --controlplane --services --arp --leaderElection | tee /etc/kubernetes/manifests/kube-vip.yaml
