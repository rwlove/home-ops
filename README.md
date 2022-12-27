## Overview
This is the configuration for my home Kubernetes cluster. It's based on the incredible [k8s-at-home template](https://github.com/k8s-at-home/template-cluster-k3s).

## Infrastructure Software
* Ansible [my roles and playbooks](https://github.com/rwlove/ansible)
* Calico Networking
* Flux2

---

## :gear:&nbsp; Hardware

| Hostname  | Device          | CPU | RAM    | Role       | Devices                      |
| --------- | --------------- | --- | ------ | ---------- | ---------------------------- |
| master1   | Intel NUC7PJYH  | 4   | 8  GB  | k8s Master |                              |
| master2   | VM on beast     | 2   | 24 GB  | k8s Master |                              |
| master3   | VM on beast     | 2   | 24 GB  | k8s Master |                              |
| worker1   | Intel DN2820FYK | 2   | 8  GB  | k8s Worker | zstick6, wyze                |
| worker2   | VM on beast     | 8   | 16 GB  | k8s Worker | ceph volume                  |
| worker3   | VM on brain     | 4   | 12 GB  | k8s Worker | longhorn volume              |
| worker4   | Intel DG965WH   | 2   | 8  GB  | k8s Worker |                              |
| worker5   | VM on beast     | 6   | 16 GB  | k8s Worker | ceph volume                  |
| worker6   | VM on beast     | 8   | 32 GB  | k8s Worker | ceph volume,wg               |
| worker7   | VM on beast     | 8   | 64 GB  | k8s Worker | coral TPU                    |
| worker8   | VM on beast     | 8   | 32 GB  | k8s Worker | zstick7, longhorn volume     |
| worker9   | VM on beast     | 8   | 32 GB  | k8s Worker | ceph volume, longhorn volume |

## Upgrades
### Flux
do not bootstrap, after initial bootstrap, as it use as it overwrites the sops code in gotk-sync.yaml. Instead use:
 `flux install --export ... > gotk-components.yaml`

## Initialization
### Provision the nodes
`./provision_cluster.sh` in [my ansible project](https://github.com/rwlove/ansible)

### Kube-VIP
[Reference](https://kube-vip.io/docs/installation/static/)
Run the following commands on master1.

`export VIP=192.168.6.1`

`export INTERFACE=eno1`

`alias kube-vip="docker run --network host --rm plndr/kube-vip:0.3.1"`

`kube-vip manifest pod --interface $INTERFACE --vip $VIP --controlplane --services --arp --leaderElection | tee /etc/kubernetes/manifests/kube-vip.yaml`

### Create the cluster
`./create-cluster.sh` in [my kubernetes project](https://github.com/rwlove/kubernetes)

### Create Calico Networking
`./create-calico-networking.sh` in [my kubernetes project](https://github.com/rwlove/kubernetes)

### Initialize Flux Cluster
`./initialize-cluster.sh`

## Teardown
### Teardown Calico Networking
`./destroy-calico-networking.sh` in [my ansible project](https://github.com/rwlove/ansible)

### Teardown the Cluster
`./destroy-cluster.sh` in [my ansible project](https://github.com/rwlove/ansible)

## Debugging
* https://kubernetes.io/docs/tasks/administer-cluster/dns-debugging-resolution/
* https://dnschecker.org
* https://kubernetes.io/docs/tasks/access-application-cluster/port-forward-access-application-cluster/
* https://github.com/nicolaka/netshoot

## Github Webhook
`kubectl -n flux-system get receivers.notification.toolkit.fluxcd.io` generates token URL to be put into
github.com -> Settings -> Webhooks -> Payload URL

* Content Type: application/json
* Secret: <token from kubectl -n flux-system describe secrets github-webhook-token>
* SSL: Enable SSL verification
* Which events would you like to trigger this webhook?: Just the push event.
* Active: <checked>
 
 ## Notes
 To get metrics-server to work with kubeadm, you need to do the following if it isn't setup with the clusterconfig provided to kubeadm
 https://kubernetes.io/docs/tasks/administer-cluster/kubeadm/kubeadm-certs/#kubelet-serving-certs
 * Need to pull the kubeadm configuration into this repository
 
 
 ## Hacks
 My Omada Controller currently needs the MixedProtocolLBService feature gate added to the kube api-server. I have not yet figured out how to enable this with kubeadm, so I'm manually updating the kube api-server manifest and restarting the kube api-server, as such:

* Add `- --feature-gates=MixedProtocolLBService=true` to /etc/kubernetes/manifests/kube-apiserver.yaml on each master node (for example: master1)
* kubectl -n kube-system delete pods kube-apiserver-master1.thesteamedcrab.com
