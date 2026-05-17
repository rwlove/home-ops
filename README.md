<div align="center">

<img src="https://github.com/rwlove/home-ops/blob/870b6ed06e5700d2c0766d712f134da86de39b2e/docs/assets/Cosmo.jpg?raw=true" width="160px" height="160px" alt="Cosmo"/>

# Lovenet Home Operations Repository

_Production-grade Kubernetes for a household._
**GitOps** with Flux · **Automated dependency updates** with Renovate · **Self-hosted by design**

<br/>

[![Renovate](https://img.shields.io/github/actions/workflow/status/rwlove/home-ops/renovate.yaml?branch=main&label=Renovate&logo=renovatebot&style=for-the-badge&color=1A1F6C)](https://github.com/rwlove/home-ops/actions/workflows/renovate.yaml)
[![Flux](https://img.shields.io/badge/Flux-managed-5468FF?style=for-the-badge&logo=flux&logoColor=white)](https://fluxcd.io)
[![Documentation](https://img.shields.io/badge/docs-mdBook-success?style=for-the-badge&logo=mdbook&logoColor=white)](https://rwlove.github.io/home-ops/)
[![Check Links](https://img.shields.io/github/actions/workflow/status/rwlove/home-ops/lychee.yaml?label=links&style=for-the-badge)](https://github.com/rwlove/home-ops/actions/workflows/lychee.yaml)

<br/>

![apps](https://img.shields.io/badge/apps-168-blue?style=for-the-badge)
![helmreleases](https://img.shields.io/badge/HelmReleases-180-326CE5?style=for-the-badge&logo=helm&logoColor=white)
![nodes](https://img.shields.io/badge/k8s_nodes-10-326CE5?style=for-the-badge&logo=kubernetes&logoColor=white)
![cnpg](https://img.shields.io/badge/Postgres_clusters-25-336791?style=for-the-badge&logo=postgresql&logoColor=white)
![secrets](https://img.shields.io/badge/secrets-109-0572EC?style=for-the-badge&logo=1password&logoColor=white)
![age](https://img.shields.io/badge/cluster_age-5%2B_years-success?style=for-the-badge)

</div>

---

## 📖 Overview

This is the live configuration for a multi-node Kubernetes cluster that runs a household — home automation, security cameras, media, document management, AI workloads, and the operational tooling required to keep it all up. Every change lands in Git first; Flux reconciles the cluster from there, and Renovate keeps dependencies current via PRs.

The repo is GitOps-strict: applications are declared as `HelmRelease` resources, secrets are pulled from 1Password through External Secrets Operator, and clusters are mostly identical except for app selection and sizing. Operational quirks, durability tiers, and security defaults live alongside the manifests in [`.agents/instructions/`](.agents/instructions/) so the conventions are enforceable, not folklore.

---

## 🗺️ Architecture

```mermaid
flowchart LR
    Dev[👤 Operator] -->|git push| Repo[(📦 GitHub<br/>home-ops)]
    Renovate[🤖 Renovate] -.->|automated PRs| Repo
    Repo -->|reconciles| Flux[⚙️ Flux]
    Flux -->|deploys| Cluster[☸️ Kubernetes<br/>10 nodes · 168 apps]

    Cluster --> Ceph[(🪨 Ceph<br/>block · default durable)]
    Cluster --> LH[(🐂 Longhorn<br/>+ recurring backups)]
    Cluster --> Garage[(🧺 Garage<br/>S3-compatible)]
    Cluster --> NFS[(🗄️ NFS<br/>beast / brain · bulk media)]

    LH -->|weekly + monthly| NFS
    Garage -->|rclone CronJobs| AWS[☁️ AWS S3<br/>Glacier Deep Archive<br/>offsite DR]

    classDef store fill:#1e293b,stroke:#475569,color:#e2e8f0
    class Ceph,LH,Garage,NFS,AWS store
```

Storage tiers are picked deliberately per workload — see [`storage-class.instructions.md`](.agents/instructions/storage-class.instructions.md) for the decision tree.

---

## 🧰 Stack at a glance

| Layer            | Tool                                | Role                                                  |
|------------------|-------------------------------------|-------------------------------------------------------|
| **OS**           | CentOS Stream 9 / 10                | Node operating system                                 |
| **Runtime**      | cri-o + crun                        | CRI runtime + OCI runtime (C implementation)          |
| **Kubernetes**   | v1.35.4                             | Control-plane and node version                        |
| **GPU**          | NVIDIA GPU Operator + Container Toolkit | P40 driver/runtime management on worker8           |
| **GitOps**       | Flux2                               | Declarative cluster reconciliation                    |
| **Automation**   | Renovate + GitHub Actions           | Dependency PRs, link checks, self-hosted runners     |
| **CNI**          | Cilium (eBPF)                       | Networking, BGP peering, LoadBalancer pool            |
| **Ingress**      | Envoy Gateway                       | L7 gateway / HTTPRoute                                |
| **DNS**          | external-dns                        | Cloudflare + bind9 split-horizon                      |
| **TLS**          | cert-manager                        | Let's Encrypt + internal CA                           |
| **Tunnel**       | cloudflared                         | Public ingress without exposing home WAN              |
| **AuthN/Z**      | Authelia + oauth2-proxy             | OIDC SSO; 24 oauth2-proxy instances gate apps         |
| **Secrets**      | External Secrets Operator + 1Password | 109 ExternalSecrets, zero plain-text in Git         |
| **VPN**          | wg-easy                             | Operator OOB WireGuard access                         |
| **Storage**      | Rook-Ceph, Longhorn, Garage, direct NFS | Tiered by durability requirement                  |
| **Databases**    | CloudNative-PG, Dragonfly, Qdrant   | 25 Postgres clusters, KV, vector                      |
| **Observability**| kube-prometheus-stack, Loki, Grafana, HolmesGPT | Metrics, logs, dashboards, AI alert triage |
| **Images**       | ZOT                                 | Pull-through registry / local cache                   |

---

## 🖥️ Hardware

| Role | Hostname  | Device              | CPU | RAM   | OS         | Storage / Accelerators       | Notes                                 |
|------|-----------|---------------------|-----|-------|------------|------------------------------|---------------------------------------|
| 🧠   | master1   | bare-metal          | 4   | 32 GB | CentOS 10  | NVMe (Longhorn)              | Intel iGPU · RTL-SDR · control plane  |
| 🧠   | master2   | VM on **beast**     | 3   | 12 GB | CentOS 9   |                              | virtualized control plane             |
| 🧠   | master3   | VM on **beast**     | 3   | 10 GB | CentOS 9   |                              | virtualized control plane             |
| 💪   | worker2   | ThinkCentre M910x   | 8   | 32 GB | CentOS 9   | NVMe (Longhorn + Ceph OSD)   | ZWA-2 Z-Wave dongle                   |
| 💪   | worker3   | ThinkCentre M910x   | 8   | 64 GB | CentOS 9   | NVMe (Longhorn + Ceph OSD)   | Sonoff Zigbee dongle                  |
| 💪   | worker4   | ThinkCentre M910x   | 8   | 32 GB | CentOS 9   | NVMe (Longhorn + Ceph OSD)   | Coral USB TPU                         |
| 💪   | worker5   | VM on **beast**     | 10  | 24 GB | CentOS 9   | NVMe (Longhorn + Ceph OSD)   |                                       |
| 💪   | worker6   | VM on **beast**     | 10  | 30 GB | CentOS 9   | NVMe (Longhorn + Ceph OSD)   |                                       |
| 💪   | worker7   | VM on **beast**     | 10  | 30 GB | CentOS 9   | NVMe (Longhorn + Ceph OSD)   |                                       |
| 🎮   | worker8   | VM on **beast**     | 10  | 55 GB | CentOS 9   | NVMe (Longhorn + Ceph OSD)   | NVIDIA **P40** (24 GB VRAM)           |

**Off-cluster infrastructure**

| Host    | Role                                                                                          |
|---------|-----------------------------------------------------------------------------------------------|
| `beast` | Dell R730xd · iDRAC 8 · RAID6 bulk storage · primary NFS · Longhorn backup target · Garage substrate · VM host |
| `brain` | Router/gateway · RAID6 mass_storage · NFS for downloads & TV · OOB SSH on `:3231`              |

---

## 🌐 Network

<details>
<summary><b>Physical topology</b> (click to expand)</summary>

<br/>

<img src="https://github.com/rwlove/home-ops/blob/main/docs/assets/physical-network-diagram.jpg" width="640px" alt="physical network diagram"/>

</details>

| Network                                        | CIDR                  | VLAN |
|------------------------------------------------|-----------------------|------|
| Default                                        | `192.168.0.0/16`      | 0    |
| IoT                                            | `10.10.20.0/24`       | 20   |
| Guest                                          | `10.10.30.0/24`       | 30   |
| Security (cameras)                             | `10.10.40.0/24`       | 40   |
| Kubernetes pod subnet (Cilium)                 | `10.42.0.0/16`        | —    |
| Kubernetes services subnet (Cilium)            | `10.43.0.0/16`        | —    |
| Kubernetes LB pool (CiliumLoadBalancerIPPool)  | `10.45.0.0/24`        | —    |

Worker nodes attach to **iot** and **sec** VLANs via Multus for direct camera and IoT-device reachability. Cilium peers BGP with the upstream router to advertise the LB pool; external ingress flows through Envoy Gateway behind cloudflared.

---

## 📦 What's running

<details>
<summary>🏠 <b>Home Automation</b> — Home Assistant ecosystem, 400+ devices</summary>

| App | Purpose |
|-----|---------|
| **Home Assistant** | Primary orchestrator; 400+ Z-Wave / Zigbee / Matter / ESPHome devices |
| **ESPHome** | Build & deploy firmware for DIY sensors |
| **EMQX** | MQTT broker |
| **Node-RED** | Visual automation flows |
| **Zigbee2MQTT** | Zigbee bridge (Sonoff stick on worker3) |
| **Z-Wave JS UI** | Z-Wave bridge (ZWA-2 stick on worker1) |
| **Matter Server** | Matter protocol bridge |
| **Frigate** | NVR + ML camera analysis (7+ cameras, Frigate+ trained model) |
| **n8n** | Workflow automation (AlertManager → HolmesGPT, etc.) |
| **NetBox** | IPAM / DCIM |
| **wyoming-services** | Piper TTS + Whisper STT for voice |
| **smtp-relay** | Maddy → Mailgun outbound mail |

</details>

<details>
<summary>🎬 <b>Media & Entertainment</b> — Jellyfin, Immich, Music Assistant, RomM</summary>

| App | Purpose |
|-----|---------|
| **Jellyfin** | Primary media server (read-only metadata) |
| **Immich** + **immich-pet-tagger** + **immichkiosk** + **immich-power-tools** | Photo library with ML face/pet recognition, offsite-backed |
| **Music Assistant** + **Gonic** | Multi-room music control + Subsonic API |
| **RomM** | Retro game library (~10k ROMs) |
| **Beets** | Music library tagging |
| **cutVideo / av1corrector / videodupfinder / medialyze** | Custom video tooling |
| **Theme Park** | Consistent UI theming across apps |
| **Batocera Webdashboard Pro** | Retro-gaming console dashboard |
| **kodi-playback-watcher** | Bridge for Kodi playback state |

</details>

<details>
<summary>🤖 <b>AI & ML</b> — Local inference, agents, image generation</summary>

| App | Purpose |
|-----|---------|
| **Ollama** | Local LLM serving on the P40 (Qwen, DeepSeek-R1, etc.) |
| **ComfyUI** | Image generation workflows |
| **Khoj** | Personal AI assistant over notes + docs |
| **LangGraph Agents** | Custom multi-agent framework (errand_runner et al.) |
| **KubeClaw** | Multi-agent platform (in development) |
| **MCP Inspector** | Model Context Protocol debugger |
| **HolmesGPT** | AI-driven alert triage (AlertManager webhook → root cause) |
| **Paperless-AI** | Auto-tagging for paperless-ngx |
| **sync-receiver** | Cross-host AI state sync |

</details>

<details>
<summary>📊 <b>Observability</b> — Prom/Loki/Grafana with AI triage on top</summary>

| App | Purpose |
|-----|---------|
| **kube-prometheus-stack** | Prometheus + AlertManager + node-exporter |
| **Loki** | Log aggregation |
| **Grafana** | Dashboards + alerting UI |
| **HolmesGPT** | LLM-backed alert investigation |
| **kube-state-metrics / kube-ops-view** | Cluster state & visualization |
| **Goldilocks** | VPA-driven resource right-sizing recommendations |
| **Kromgo** | Prometheus → Glance dashboard bridge |
| **Netdata** | Per-node real-time metrics |
| **network-ups-tools (NUT)** | UPS monitoring & graceful shutdown |
| **exporters** | Custom Prometheus exporters |

</details>

<details>
<summary>🗄️ <b>Data & Storage</b> — Databases, object storage, vector search</summary>

| App | Purpose |
|-----|---------|
| **CloudNative-PG** | 25 Postgres clusters with WAL archiving to Garage |
| **Dragonfly** | Redis-compatible in-memory store |
| **Qdrant** | Vector DB for embeddings / RAG |
| **pgAdmin** | Postgres admin UI |
| **Rook-Ceph** | Distributed block storage (default durable tier) |
| **Longhorn** | Block storage with NFS-backed recurring backups |
| **Garage** | S3-compatible object storage (DB backups, app S3 workloads) |

</details>

<details>
<summary>🌐 <b>Network, Auth & Platform</b> — Ingress, SSO, GitOps machinery</summary>

| App | Purpose |
|-----|---------|
| **Cilium** | CNI, BGP, LoadBalancer pool |
| **Envoy Gateway** | Ingress / HTTPRoute (30 routes) |
| **cert-manager** | TLS certificate lifecycle |
| **external-dns** | Cloudflare + bind9 record sync |
| **cloudflared** | Public tunnel without exposed WAN |
| **Authelia** | OIDC identity provider (LLDAP backend) |
| **oauth2-proxy** | 24 instances gating per-app SSO |
| **wg-easy** | Primary OOB WireGuard access |
| **External Secrets Operator** | 1Password-backed secret materialization |
| **Flux2** | GitOps reconciler |
| **Renovate** | Image & Helm chart update PRs |
| **Kuadrant** | MCP server gateway (Authelia-gated JWT) |
| **actions-runner-controller** | Self-hosted GitHub Actions runners |
| **ZOT** | Pull-through registry cache |

</details>

<details>
<summary>🗂️ <b>Documents & Collaboration</b> — Personal knowledge stack</summary>

| App | Purpose |
|-----|---------|
| **Paperless-ngx** | Document scanning, OCR, tagging (CNPG-backed, offsite-backed) |
| **Obsidian** + **obsidian-couchdb** | Notes sync (CouchDB w/ Cloudflare rate-limiting) |
| **Zulip** | Self-hosted team chat |
| **Mealie** | Recipe management |

</details>

---

## ☁️ Cloud dependencies

| Service                                          | Use                                                              | Cost              |
|--------------------------------------------------|------------------------------------------------------------------|-------------------|
| [1Password](https://1password.com/)              | Secret backend for External Secrets                              | ~$65 / yr         |
| [Cloudflare](https://www.cloudflare.com/)        | Domain, DNS, tunnel, WAF rate-limiting                           | Free              |
| [GitHub](https://github.com/)                    | Repo hosting + CI                                                | Free              |
| [Mailgun](https://www.mailgun.com/)              | Outbound mail relay (via Maddy)                                  | Free (Flex)       |
| [Pushover](https://pushover.net/)                | Push notifications for AlertManager + apps                       | $10 one-time      |
| [Frigate+](https://plus.frigate.video/)          | Trained ML model for Frigate NVR                                 | $50 / yr          |
| [AWS S3 Glacier Deep Archive](https://aws.amazon.com/glacier/) | Offsite DR for Immich + Paperless (objects + DB backups) | ~$1–5 / mo (varies) |
|                                                  |                                                                  | **~$10–15 / mo**  |

---

## 🛡️ Operational pillars

#### 💾 Tiered storage durability
Four tiers, picked by what the data has to survive — node loss, Ceph loss, cluster loss, or full site loss. Databases get `ceph-block` + Barman→Garage; irreplaceable state goes to Longhorn with NFS-shipped weekly + monthly backups; S3-shaped workloads use Garage; bulk media rides direct NFS. Full decision tree: [`.agents/instructions/storage-class.instructions.md`](.agents/instructions/storage-class.instructions.md).

#### 🔐 Secrets — zero plain-text in Git
All 109 ExternalSecrets resolve through External Secrets Operator from 1Password. Application credentials are templated into `ExternalSecret` resources and never live in YAML. Cross-namespace mirrors use the reflector pattern when consumer charts hard-code secret names.

#### 🪪 Authentication — single sign-on everywhere
Authelia (with LLDAP) is the OIDC identity provider; per-app oauth2-proxy instances enforce auth at Envoy Gateway. 24 apps sit behind SSO today. The mcp-gateway validates Authelia-issued JWTs with a daily-rotated signing key for MCP tooling.

#### 🔭 Observability — metrics, logs, AI triage
kube-prometheus-stack scrapes everything; Loki ingests pod logs; Grafana stitches the dashboards. AlertManager fans alerts to Pushover and to **HolmesGPT**, which runs LLM-driven root-cause investigation against the cluster and posts findings back via n8n.

#### 🎮 GPU workloads
A single NVIDIA P40 (24 GB VRAM) on worker8 drives Ollama (local LLM), ComfyUI (image gen), Whisper STT, Immich's CLIP face/pet recognition, and the immich-pet-tagger fork pinned to a P40-compatible PyTorch build. Driver lifecycle is handled by the NVIDIA GPU Operator.

#### 🛟 Disaster recovery
Per-app rclone CronJobs ship Immich originals and Paperless documents — plus their Garage-stored Postgres backups — to encrypted AWS S3 with a 1-day Glacier Deep Archive transition. Recovery procedure is documented at [`docs/src/offsite_recovery.md`](docs/src/offsite_recovery.md) and was last validated 2026-05-05.

#### 🌪️ Strict GitOps
Every change reaches the cluster through Git. Flux suspends are a deliberate manual signal — paused Kustomizations are not "broken," they're intentional pauses for in-flight maintenance and are documented in conventions, not reverted on sight.

---

## 📚 Documentation

The full operator handbook lives in the mdBook site: **<https://rwlove.github.io/home-ops/>**.

Frequently referenced pages:

- [Cluster rebuild](https://rwlove.github.io/home-ops/cluster_rebuild.html)
- [Initialization & teardown](https://rwlove.github.io/home-ops/init_teardown.html)
- [Cluster upgrade](https://rwlove.github.io/home-ops/cluster_upgrade.html)
- [Power outage recovery](https://rwlove.github.io/home-ops/power-outage.html)
- [Limits & requests philosophy](https://rwlove.github.io/home-ops/limits.html)
- [Debugging playbook](https://rwlove.github.io/home-ops/debugging.html)
- [Offsite recovery](https://rwlove.github.io/home-ops/offsite_recovery.html)
- [Immich restore to new CNPG database](https://rwlove.github.io/home-ops/immich_cnpg.html)
- [NVIDIA P40 GPU setup](https://rwlove.github.io/home-ops/p40.html)
- [master1 etcd disk swap](https://rwlove.github.io/home-ops/master1_etcd_disk_swap.html)
- [GitHub webhook](https://rwlove.github.io/home-ops/github_webhook.html)

Repo-local conventions (auto-loaded by AI agents from [`.agents/instructions/`](.agents/instructions/)):

- Storage class selection · HelmRelease security defaults · ConfigMap layout · Sorting rules · Schema correction · Persona

---

## 🙏 Acknowledgements

Inspired by the [k8s-at-home](https://github.com/k8s-at-home) community. [@whazor](https://github.com/whazor) maintains the excellent [k8s-at-home search](https://nanne.dev/k8s-at-home-search/) — a great way to discover how others configure the same Helm releases.

<div align="center">
<sub>This repo has been continuously reconciling itself since <b>March 2021</b>.</sub>
</div>
