<div align="center">

<img src="https://github.com/rwlove/home-ops/blob/870b6ed06e5700d2c0766d712f134da86de39b2e/docs/assets/Cosmo.jpg?raw=true" width="144px" height="144px"/>

## My Home Operations Repository

_Managed by Flux, Renovate and GitHub Actions_ :robot:

[![Renovate](https://img.shields.io/github/actions/workflow/status/rwlove/home-ops/renovate.yaml?branch=main&label=&logo=renovatebot&style=for-the-badge&color=blue)](https://github.com/rwlove/home-ops/actions/workflows/renovate.yaml)

Main Kubernetes Cluster Information:

[![Age-Days](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.thesteamedcrab.com%2Fquery%3Fformat%3Dendpoint%26metric%3Dmain_cluster_age_days&style=flat-square&label=Age)](https://github.com/kashalls/kromgo/)&nbsp;
[![Node-Count](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.thesteamedcrab.com%2Fquery%3Fformat%3Dendpoint%26metric%3Dmain_cluster_node_count&style=flat-square&label=Nodes)](https://github.com/kashalls/kromgo/)&nbsp;
[![Pod-Count](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.thesteamedcrab.com%2Fquery%3Fformat%3Dendpoint%26metric%3Dmain_cluster_pod_count&style=flat-square&label=Pods)](https://github.com/kashalls/kromgo/)&nbsp;
[![CPU-Usage](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.thesteamedcrab.com%2Fquery%3Fformat%3Dendpoint%26metric%3Dmain_cluster_cpu_usage&style=flat-square&label=CPU)](https://github.com/kashalls/kromgo/)&nbsp;
[![Memory-Usage](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.thesteamedcrab.com%2Fquery%3Fformat%3Dendpoint%26metric%3Dmain_cluster_memory_usage&style=flat-square&label=Memory)](https://github.com/kashalls/kromgo/)&nbsp;

</div>
<br><br>

## Overview
This is the configuration for my GitOps homelab Kubernetes cluster. This cluster runs home software services for my residence. It is quite complex and there are a lot of interdependencies but the declarative nature of GitOps allows me to manage this mesh of code. The software services fall into a few primary categories:
* Home Automation ([Home Assistant](https://www.home-assistant.io/), [ESPHome](https://esphome.io/), [WLED](https://kno.wled.ge/))
* Home Metering and Monitoring (Weather Station, Power Monitoring, Sensors)
* Home Security ([Frigate](https://frigate.video/), [Double Take](https://github.com/jakowenko/double-take), [Konnected](https://konnected.io/))

## Core Components
### Infrastructure
- [CentOS 9 Stream](https://www.centos.org/centos-stream/): Kubernetes Node Operating System.
- [crun](https://github.com/containers/crun): Container Runtime implemented in C.

### Networking
- [cilium](https://cilium.io/): Kubernetes Container Network Interface (CNI).
- [cert-manager](https://cert-manager.io/docs/): Creates SSL certificates for services in my Kubernetes cluster.
- [external-dns](https://github.com/kubernetes-sigs/external-dns): Automatically manages DNS records from my cluster in a cloud DNS provider.
- [ingress-nginx](https://github.com/kubernetes/ingress-nginx/): Ingress controller to expose HTTP traffic to pods over DNS.

### Storage
- [Rook-Ceph](https://github.com/rook/rook): Distributed block storage for peristent storage..
- [Minio](https://min.io/): S3 Compatible Storage Interface.
- [Longhorn](https://longhorn.io/): Cloud native distributed block storage for Kubernetes.
- [NFS](https://github.com/kubernetes-sigs/nfs-subdir-external-provisioner): NFS storage.

### GitOps
- [Flux2](https://github.com/fluxcd/flux2): Declarative Cluster GitOps
- [actions-runner-controller](https://github.com/actions/actions-runner-controller): Self-hosted Github runners.
- [sops](https://toolkit.fluxcd.io/guides/mozilla-sops/): Managed secrets for Kubernetes which are commited to Git.
- [Rennovate](https://github.com/renovatebot/renovate): Automated Cluster Management.

---

## :gear:&nbsp; Hardware

| Hostname  | Device            | CPU | RAM    | OS       |Role        | Storage                 | IOT        | Network  |
| --------- | ----------------- | --- | ------ | -------- | ---------- | ----------------------- | ---------- | -------- |
| master1   | Intel NUC7PJYH    | 4   | 8  GB  | CentOS 9 | k8s Master |                         |            |          |
| master2   | VM on beast       | 3   | 8  GB  | CentOS 9 | k8s Master |                         |            |          |
| master3   | VM on beast       | 3   | 8  GB  | CentOS 9 | k8s Master |                         |            |          |
| worker1   | ThinkCentre M910x | 8   | 32 GB  | CentOS 9 | k8s Worker | longhorn NVMe           | Coral USB  |          |
| worker2   | ThinkCentre M910x | 8   | 32 GB  | CentOS 9 | k8s Worker | longhorn NVMe           | zstick-7   |          |
| worker3   | ThinkCentre M910x | 8   | 32 GB  | CentOS 9 | k8s Worker | longhorn NVMe, ceph osd |            | sec-vlan |
| worker4   | ThinkCentre M910x | 8   | 32 GB  | CentOS 9 | k8s Worker | longhorn NVMe           |            | sec-vlan |
| worker5   | VM on beast       | 10  | 24 GB  | CentOS 9 | k8s Worker | longhorn NVMe, ceph osd |            |          |
| worker6   | VM on beast       | 10  | 24 GB  | CentOS 9 | k8s Worker | longhorn NVMe, ceph osd | skyconnect |          |
| worker7   | VM on beast       | 10  | 24 GB  | CentOS 9 | k8s Worker | longhorn NVMe, ceph osd |            | iot-vlan |
| worker8   | VM on beast       | 10  | 24 GB  | CentOS 9 | k8s Worker | longhorn NVMe, ceph osd |            | iot-vlan |

## Network
<details>
  <summary>Click to see a high level physical network diagram</summary>

  <img src="https://github.com/rwlove/home-ops/blob/5d052422d64299f22c499d7bd2768f1ac58e71f6/docs/assets/physical-network-diagram.jpg" align="center" width="600px" alt="dns"/>
</details>

| Name                                           | CIDR                       | VLAN | Notes |
|------------------------------------------------|----------------------------| ---- | ----- |
| Management VLAN                                |                            |      | TBD   |
| Default                                        | `192.168.0.0/16`           |  0   |       |
| IOT VLAN                                       | `10.10.20.1/24`            | 20   |       |
| Guest VLAN                                     | `10.10.30.1/24`            | 30   |       |
| Security VLAN                                  | `10.10.40.1/24`            | 40   |       |
| Kubernetes Pod Subnet (Cilium)                 | `10.42.0.0/16`             | N/A  |       |
| Kubernetes Services Subnet (Cilium)            | `10.43.0.0/16`             | N/A  |       |
| Kubernetes LB Range (CiliumLoadBalancerIPPool) | `10.45.0.1/24`             | N/A  |       |

## ☁️ Cloud Dependencies

| Service                                         | Use                                                               | Cost             |
|-------------------------------------------------|-------------------------------------------------------------------|------------------|
| [1Password](https://1password.com/)             | Secrets with [External Secrets](https://external-secrets.io/)     | ~$65/yr          |
| [Cloudflare](https://www.cloudflare.com/)       | Domain                                                            | Free             |
| [GitHub](https://github.com/)                   | Hosting this repository and continuous integration/deployments    | Free             |
| [Mailgun](https://www.mailgun.com/)             | Email hosting                                                     | Free (Flex Plan) |
| [Pushover](https://pushover.net/)               | Kubernetes Alerts and application notifications                   | $10 (One Time)   |
|                                                 |                                                                   | Total: ~$5.50/mo |

---

## Initialization
```./init/create-cluster.sh``` (on master)

```./init/prepare-cluster.sh``` (on laptop)

```./init/initialize-cluster.sh``` (on laptop)

```ssh root@master1 rm /etc/kubernetes/manifests/kube-vip.yaml``` (on laptop)

## Teardown
```./init/destroy-cluster.sh``` (on laptop)

## Debugging
* https://kubernetes.io/docs/tasks/administer-cluster/dns-debugging-resolution/
* https://dnschecker.org
* https://kubernetes.io/docs/tasks/access-application-cluster/port-forward-access-application-cluster/
* https://github.com/nicolaka/netshoot
* https://www.redhat.com/sysadmin/using-nfsstat-nfsiostat

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

 ### Resources: Limits and Requests Philosophy
 In short, do set CPU requests, but don't set CPU limits and set the Memory limit to be the same as the Memory requests.
 * [CPU Guidance](https://home.robusta.dev/blog/stop-using-cpu-limits)
 * [Limits Guidance](https://home.robusta.dev/blog/kubernetes-memory-limit)

 [@whazor](https://github.com/whazor) created [this website](https://nanne.dev/k8s-at-home-search/) as a creative way to search Helm Releases across GitHub. You may use it as a means to get ideas on how to configure an applications' Helm values.
