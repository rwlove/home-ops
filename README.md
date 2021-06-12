## Overview
This is the configuration for my home Kubernetes cluster. It's based on the incredible [k8s-at-home template](https://github.com/k8s-at-home/template-cluster-k3s).

## Infrastructure Software
* Ansible ([my roles and playbooks](https://github.com/rwlove/ansible))
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
