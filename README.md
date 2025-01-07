<div align="center">

<img src="https://github.com/rwlove/home-ops/blob/870b6ed06e5700d2c0766d712f134da86de39b2e/docs/assets/Cosmo.jpg?raw=true" width="144px" height="144px"/>

## Lovenet Home Operations Repository

_Managed by Flux, Renovate and GitHub Actions_ :robot:

[![Kubernetes](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.thesteamedcrab.com%2Fquery%3Fformat%3Dendpoint%26metric%3Dkubernetes_version&style=for-the-badge&logo=kubernetes&logoColor=white&color=blue&label=%20)](https://github.com/kashalls/kromgo/)&nbsp;&nbsp;
[![Renovate](https://img.shields.io/github/actions/workflow/status/rwlove/home-ops/renovate.yaml?branch=main&label=&logo=renovatebot&style=for-the-badge&color=blue)](https://github.com/rwlove/home-ops/actions/workflows/renovate.yaml)&nbsp;&nbsp;
[![Documentation](https://img.shields.io/badge/documentation-blue?&style=for-the-badge)](https://rwlove.github.io/home-ops/)&nbsp;&nbsp;

Kubernetes Cluster Information:

[![Age-Days](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.thesteamedcrab.com%2Fquery%3Fformat%3Dendpoint%26metric%3Dcluster_age_days&style=flat-square&label=Age)](https://github.com/kashalls/kromgo/)&nbsp;
[![Node-Count](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.thesteamedcrab.com%2Fquery%3Fformat%3Dendpoint%26metric%3Dcluster_node_count&style=flat-square&label=Nodes)](https://github.com/kashalls/kromgo/)&nbsp;
[![Pod-Count](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.thesteamedcrab.com%2Fquery%3Fformat%3Dendpoint%26metric%3Dcluster_pod_count&style=flat-square&label=Pods)](https://github.com/kashalls/kromgo/)&nbsp;
[![CPU-Usage](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.thesteamedcrab.com%2Fquery%3Fformat%3Dendpoint%26metric%3Dcluster_cpu_usage&style=flat-square&label=CPU)](https://github.com/kashalls/kromgo/)&nbsp;
[![Memory-Usage](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.thesteamedcrab.com%2Fquery%3Fformat%3Dendpoint%26metric%3Dcluster_memory_usage&style=flat-square&label=Memory)](https://github.com/kashalls/kromgo/)&nbsp;
[![Power-Usage](https://img.shields.io/endpoint?url=https%3A%2F%2Fkromgo.thesteamedcrab.com%2Fcluster_power_usage&style=flat-square&label=Power)](https://github.com/kashalls/kromgo)&nbsp;
[![Check Links](https://github.com/rwlove/home-ops/actions/workflows/lychee.yaml/badge.svg)](https://github.com/rwlove/home-ops/actions/workflows/lychee.yaml)

</div>
<br><br>

## Overview
This is the configuration for my GitOps homelab Kubernetes cluster. This cluster runs home software services for my residence. It is quite complex and there are a lot of interdependencies but the declarative nature of GitOps allows me to manage this mesh of code. The software services fall into a few primary categories:
* Home Automation ([Home Assistant](https://www.home-assistant.io/), [ESPHome](https://esphome.io/), [Node-Red](https://github.com/node-red/node-red), [EMQX](https://github.com/emqx/emqx), [ZWave JS UI](https://github.com/zwave-js/zwave-js-ui), [Zigbee2MQTT](https://www.zigbee2mqtt.io/))
* Home Metering and Monitoring (Weather Station, Power Monitoring, Sensors)
* Home Security ([Frigate](https://frigate.video/), [Double Take](https://github.com/jakowenko/double-take))
* IOT Devices ([WLED](https://kno.wled.ge/), [Ratgdo](https://github.com/PaulWieland/ratgdo))

## Core Components
### Infrastructure
- [CentOS 9 Stream](https://www.centos.org/centos-stream/): Kubernetes Node Operating System.
- [crun](https://github.com/containers/crun): Container Runtime implemented in C.
- [nVIDIA Container Toolkit](https://github.com/NVIDIA/nvidia-container-toolkit): Container Runtime for nVIDIA GPUs.

### Networking
- [cilium](https://cilium.io): Kubernetes Container Network Interface (CNI).
- [cert-manager](https://cert-manager.io/docs): Creates SSL certificates for services in my Kubernetes cluster.
- [external-dns](https://github.com/kubernetes-sigs/external-dns): Automatically manages DNS records from my cluster in a cloud DNS provider.
- [ingress-nginx](https://github.com/kubernetes/ingress-nginx): Ingress controller to expose HTTP traffic to pods over DNS.
- [Cloudflared](https://github.com/cloudflare/cloudflared): Cloudflare tunnel client.

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

## :gear:&nbsp; Configuration
- [Home Assistant](https://github.com/rwlove/home-assistant-config)
- [Node Red](https://github.com/rwlove/node-red-hass-flows)
- [ESPHome](https://github.com/rwlove/esphome_config)

---

## :gear:&nbsp; Hardware

| Hostname  | Device            | CPU | RAM    | OS       |Role        | Storage                 | IOT        | Network      |
| --------- | ----------------- | --- | ------ | -------- | ---------- | ----------------------- | ---------- | ------------ |
| master1   | Intel NUC7PJYH    | 4   | 8  GB  | CentOS 9 | k8s Master |                         |            |              |
| master2   | VM on beast       | 3   | 8  GB  | CentOS 9 | k8s Master |                         |            |              |
| master3   | VM on beast       | 3   | 8  GB  | CentOS 9 | k8s Master |                         |            |              |
| worker1   | ThinkCentre M910x | 8   | 32 GB  | CentOS 9 | k8s Worker | longhorn NVMe           | Z-Stick 7  | iot/sec-vlan |
| worker2   | ThinkCentre M910x | 8   | 32 GB  | CentOS 9 | k8s Worker | longhorn NVMe           |            | iot/sec-vlan |
| worker3   | ThinkCentre M910x | 8   | 32 GB  | CentOS 9 | k8s Worker | longhorn NVMe, ceph osd | Sonoff     | iot/sec-vlan |
| worker4   | ThinkCentre M910x | 8   | 32 GB  | CentOS 9 | k8s Worker | longhorn NVMe           | Coral USB  | iot/sec-vlan |
| worker5   | VM on beast       | 10  | 24 GB  | CentOS 9 | k8s Worker | longhorn NVMe, ceph osd |            | iot/sec-vlan |
| worker6   | VM on beast       | 10  | 24 GB  | CentOS 9 | k8s Worker | longhorn NVMe, ceph osd | skyconnect | iot/sec-vlan |
| worker7   | VM on beast       | 10  | 24 GB  | CentOS 9 | k8s Worker | longhorn NVMe, ceph osd |            | iot/sec-vlan |
| worker8   | VM on beast       | 10  | 48 GB  | CentOS 9 | k8s Worker | longhorn NVMe, ceph osd | nVIDIA P40 | iot/sec-vlan |

## Network
<details>
  <summary>Click to see a high level physical network diagram</summary>

  <img src="https://github.com/rwlove/home-ops/blob/main/docs/assets/physical-network-diagram.jpg" align="center" width="600px" alt="dns"/>
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
| [1Password](https://1password.com/)             | Secrets with [External Secrets](https://external-secrets.io/)     | ~$65 (1 Year)    |
| [Cloudflare](https://www.cloudflare.com/)       | Domain                                                            | Free             |
| [GitHub](https://github.com/)                   | Hosting this repository and continuous integration/deployments    | Free             |
| [Mailgun](https://www.mailgun.com/)             | Email hosting                                                     | Free (Flex Plan) |
| [Pushover](https://pushover.net/)               | Kubernetes Alerts and application notifications                   | $10 (One Time)   |
| [Frigate Plus](https://plus.frigate.video/)     | Model training services for Frigate NVR                           | $50 (1 Year)     |
|                                                 |                                                                   | Total: ~$9.60/mo

---

### Noteworthy Documentation

[Cluster Rebuild Actions](cluster_rebuild.md)&nbsp;&nbsp;
[Initialization and Teardown](https://rwlove.github.io/home-ops/init_teardown.html)&nbsp;&nbsp;
[Github Webhook](https://rwlove.github.io/home-ops/github_webhook.html)&nbsp;&nbsp;
[Limits and Requests Philosophy](https://rwlove.github.io/home-ops/limits.html)&nbsp;&nbsp;
[Debugging](https://rwlove.github.io/home-ops/debugging.html)&nbsp;&nbsp;
[Immich restore to new CNPG database](immich_cnpg.md)&nbsp;&nbsp;
[nVIDIA P40 GPU](https://rwlove.github.io/home-ops/p40.html)&nbsp;&nbsp;

### Home-Ops Search

[@whazor](https://github.com/whazor) created [this website](https://nanne.dev/k8s-at-home-search/) as a creative way to search Helm Releases across GitHub. You may use it as a means to get ideas on how to configure an applications' Helm values.
