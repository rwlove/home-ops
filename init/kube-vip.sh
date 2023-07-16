#!/bin/bash

export VIP=192.168.6.1
export INTERFACE=enp3s0

mkdir -p /etc/kubernetes/manifests/

podman run --network host --rm plndr/kube-vip:v0.6.0 manifest pod --interface $INTERFACE --vip $VIP --controlplane --services --arp --leaderElection | tee /etc/kubernetes/manifests/kube-vip.yaml
