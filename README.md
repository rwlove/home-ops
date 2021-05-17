## Overview
This is the configuration for my home Kubernetes cluster. It's based on the incredible [k8s-at-home template](https://github.com/k8s-at-home/template-cluster-k3s).

## Infrastructure Software
* Ansible ([my roles and playbooks](https://github.com/rwlove/ansible))
* Calico Networking
* Flux2

---

## :gear:&nbsp; Hardware
| Device                  | CPU   | RAM    | Role       | Hostname   |
|-------------------------|-------|---------------------|------------|
| Intel NUC7PJYH          | 4     | 8  GB  | k8s Master | master1   |
| Intel DN2820FYK         | 2     | 8  GB  | k8s Worker | worker1   |
| Intel DN2820FYK         | 2     | 8  GB  | k8s Worker | worker2   |
| Virtual Machine         | 4     | 12 GB  | k8s Worker | worker3   |
| Intel DG965WH           | 2     | 8  GB  | k8s Worker | worker4   |

---
