## Overview
This is the configuration for my home Kubernetes cluster. It's based on the incredible [k8s-at-home template](https://github.com/k8s-at-home/template-cluster-k3s).

## Infrastructure Software
* Ansible [my roles and playbooks](https://github.com/rwlove/ansible)
* Calico Networking
* Flux2

---

## :gear:&nbsp; Hardware

| Hostname  | Device          | CPU | RAM    | Role       |
| --------- | --------------- | --- | ------ | ---------- |
| master1   | Intel NUC7PJYH  | 4   | 8  GB  | k8s Master |
| master2   | Virtual Machine | 2   | 32 GB  | k8s Master |
| master3   | Virtual Machine | 2   | 32 GB  | k8s Master |
| worker1   | Intel DN2820FYK | 2   | 8  GB  | k8s Worker |
| worker2   | Intel DN2820FYK | 2   | 8  GB  | k8s Worker |
| worker3   | Virtual Machine | 4   | 12 GB  | k8s Worker |
| worker4   | Intel DG965WH   | 2   | 8  GB  | k8s Worker |
| worker5   | Virtual Machine | 2   | 32 GB  | k8s Worker |
| worker6   | Virtual Machine | 2   | 32 GB  | k8s Worker |
| worker7   | Virtual Machine | 2   | 32 GB  | k8s Worker |

## Upgrades
### Flux
do not bootstrap, after initial bootstrap, as it use as it overwrites the sops code in gotk-sync.yaml. Instead use:
 `flux install --export ... > gotk-components.yaml`

## Initialization
### Provision the nodes
`./provision_cluster.sh` in [my ansible project](https://github.com/rwlove/ansible)

### Kube-VIP
[Reference](https://kube-vip.io/hybrid/static/)
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
