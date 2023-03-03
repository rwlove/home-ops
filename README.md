## Overview
This is the configuration for my GitOps homelab Kubernetes cluster. This cluster runs home software services for my residence. It is quite complex and there are a lot of interdependencies but the declarative nature of GitOps allows me to manage this mesh of code. The software services fall into a few primary categories:
* Home Automation ([Home Assistant](https://www.home-assistant.io/), [ESPHome](https://esphome.io/), [WLED](https://kno.wled.ge/))
* Media Management ([Kodi](https://kodi.tv/))
* Home Metering and Monitoring (Weather Station, Power Monitoring, Sensors)
* Home Security ([Frigate](https://frigate.video/), [Double Take](https://github.com/jakowenko/double-take), [Konnected](https://konnected.io/))

## Core Components
### Infrastructure
- [CentOS 8 Stream](https://www.centos.org/centos-stream/): Kubernetes Node Operating System.
- [crun](https://github.com/containers/crun): Container Runtime implemented in C.

### Networking
- [cilium](https://cilium.io/): Kubernetes Container Network Interface (CNI).
- [cert-manager](https://cert-manager.io/docs/): Creates SSL certificates for services in my Kubernetes cluster.
- [external-dns](https://github.com/kubernetes-sigs/external-dns): Automatically manages DNS records from my cluster in a cloud DNS provider.
- [ingress-nginx](https://github.com/kubernetes/ingress-nginx/): Ingress controller to expose HTTP traffic to pods over DNS.
- [Traefik](https://traefik.io/): Ingress Controller.

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

| Hostname  | Device          | CPU | RAM    | Role       | Devices                                       |
| --------- | --------------- | --- | ------ | ---------- | --------------------------------------------- |
| master1   | Intel NUC7PJYH  | 4   | 8  GB  | k8s Master |                                               |
| master2   | VM on beast     | 2   | 24 GB  | k8s Master |                                               |
| master3   | VM on beast     | 2   | 24 GB  | k8s Master |                                               |
| worker1   | Intel DN2820FYK | 2   | 8  GB  | k8s Worker | zstick6, wyze                                 |
| worker2   | VM on beast     | 8   | 16 GB  | k8s Worker | ceph volume                                   |
| worker3   | VM on brain     | 6   | 12 GB  | k8s Worker |                                               |
| worker4   | Intel DG965WH   | 2   | 8  GB  | k8s Worker |                                               |
| worker5   | VM on beast     | 6   | 16 GB  | k8s Worker | ceph volume                                   |
| worker6   | VM on beast     | 8   | 32 GB  | k8s Worker | ceph volume,wg                                |
| worker7   | VM on beast     | 8   | 64 GB  | k8s Worker | coral TPU, longhorn volume (NVMe)             |
| worker8   | VM on beast     | 8   | 32 GB  | k8s Worker | zstick7, longhorn volume (NVMe)               |
| worker9   | VM on beast     | 8   | 32 GB  | k8s Worker | ceph volume, longhorn volume (NVMe), iot-vlan |

## Network
<details>
  <summary>Click to see a high level physical network diagram</summary>

  <img src="https://github.com/rwlove/fleet-infra/blob/5d052422d64299f22c499d7bd2768f1ac58e71f6/docs/assets/physical-network-diagram.jpg" align="center" width="600px" alt="dns"/>
</details>

| Name                                  | CIDR                       | VLAN | Notes |
|---------------------------------------|----------------------------| ---- | ----- |
| Management VLAN                       |                            |      | TBD   |
| Default                               | `192.168.0.0/16`           |  0   |       |
| IOT VLAN                              | `10.10.20.1/24`            | 20   |       |
| Guest VLAN                            | `10.10.30.1/24`            | 30   |       |
| Security VLAN                         | `10.10.40.1/24`            | 40   |       |
| Kubernetes Pod Subnet (Cilium)        | `11.0.0.0/8`               | N/A  |       |
| Kubernetes Services Subnet (Cilium)   | `11.96.0.0/12`             | N/A  |       |
| Kubernetes MetalLB Range              | `192.168.6.2-192.168.6.250`| N/A  |       |

## Initialization
```./init/create-cluster.sh``` (on master)

```kubectl apply -f clusters/lovenet/core/kube-system/cilium-quick-install/quick-install.yaml```

```./initialize-cluster.sh```

Remove master1 kube-vip static manifest

## Teardown
```./init/destroy-cluster.sh```

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
