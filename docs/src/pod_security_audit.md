# Pod Security Baseline Audit

Status: **Audit only — no remediation in this PR.** Next step is per-namespace remediation PRs; enforcement via Kyverno/PSA is a separate later step.
Owner: home-ops
Last updated: 2026-05-18

Audits running pods against the baseline defined in
[`.agents/instructions/helmrelease.security.md`](https://github.com/rwlove/home-ops/blob/main/.agents/instructions/helmrelease.security.md).

## Scope

Snapshot taken 2026-05-18. Audited 422 pods / 524 containers across 20
namespaces. Excluded `kube-system`, `flux-system`, `istio-system`, and
`cilium-secrets` (perimeter/system control plane; out of scope for this
audit's threat model — same rationale as the network policy rollout).

Workload classes:

| Class | Containers | Notes |
|---|---|---|
| User app (HelmRelease-controlled) | 325 | 18 namespaces; we own the values.yaml |
| Infra operator (rook-ceph, longhorn-system) | 199 | Operator-controlled pod templates; deviations mostly inherent |

The audit baseline:

**Pod-level**: `runAsNonRoot: true`, non-zero `runAsUser`/`runAsGroup`, `fsGroup`, `fsGroupChangePolicy: OnRootMismatch`.
**Container-level**: `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, `capabilities.drop: [ALL]`, `seccompProfile.type: RuntimeDefault`.

## Findings by deviation

Counts below are containers (not pods — DaemonSet replicas inflate
pod counts), split into user-app vs infra-operator. Workload-level
remediation effort is keyed off **distinct HelmRelease**, not container
count.

| Deviation | User-app | Infra-op | Total |
|---|---:|---:|---:|
| Defaults to root (no `runAsUser`, no `runAsNonRoot`) | 99 | n/a | 99 |
| Explicit `runAsUser: 0` | 32 | 17 | 49 |
| `readOnlyRootFilesystem` != true | 151 | 198 | 349 |
| Privileged container | 26 | 91 | 117 |
| Missing `capabilities.drop: [ALL]` | 124 | 165 | 289 |
| Missing `seccompProfile` (effective) | 197 | 199 | 396 |
| Explicit `allowPrivilegeEscalation: true` | 0 | 24 | 24 |
| Capabilities added (`NET_ADMIN`, `SYS_ADMIN`, etc.) | 19 | 24 | 43 |
| `fsGroup` set without `OnRootMismatch` | 24 (HR-owned) | — | 24 |

54 user-app containers (~17%) pass every baseline check today.

## Group A — Easy remediation (chart-values change, low risk)

### A1. oauth2-proxy HelmReleases (24 instances, all violate everything)

`quay.io/oauth2-proxy/oauth2-proxy` containers across 24 HelmReleases
have **zero** `securityContext` set. The binary is a stateless
reverse proxy — runs fine as non-root, doesn't write to rootfs, doesn't
need any caps.

**Proposed**: stamp the baseline pod + container `securityContext` onto
every `*-oauth2-proxy/app/helmrelease.yaml`. Affected:

```text
ai/khoj-oauth2-proxy
collab/{glance,glance-user,pump,startpunkt}-oauth2-proxy
collab/garage-webui-oauth2-proxy (under storage/)
downloads/{prowlarr,jdownloader2,qbittorrent,sabnzbd,slskd}-oauth2-proxy
home/frigate-oauth2-proxy
media/{av1corrector,lidarr,medialyze,music-assistant,radarr,sonarr,soularr,
       suggestarr,batocera-webdashboard-pro}-oauth2-proxy
observability/{goldilocks,kube-ops-view,holmesgpt}-oauth2-proxy
storage/garage-webui-oauth2-proxy
```

Single PR per app to keep blast radius bounded, or one PR scoping the
entire family — the latter is cheaper and the risk is uniform.

### A2. `*arr` and similar app-template charts missing baseline

Apps where the bjw-s `defaultPodOptions` aren't being inherited at the
container level. Likely candidates for one-PR-each remediation:

- `collab/{it-tools,nametag,paperless,pump,pump-cv,swiparr,kitchenowl}`
- `media/{flaresolverr,immich-power-tools,videodupfinder,theme-park,immichkiosk}`
- `home/wyoming-services-{kokoro,openwakeword,whisper}`
- `ai/{kubeclaw-qmd,kubeclaw-qmd-update,paperless-ai,sync-receiver}`

Each is a stateless or near-stateless app reading from a configMap or
PVC; readOnlyRootFilesystem should land with at most a `tmpfs`
`/tmp` mount.

### A3. `fsGroupChangePolicy` missing on HR-owned pods

24 HelmRelease-owned workloads set `fsGroup` but not
`fsGroupChangePolicy: OnRootMismatch`. Cosmetic for already-chowned
PVCs, but adds startup latency on large volumes (Immich, paperless,
n8n). One-line fix per app:

```text
ai/{kubeclaw-chromium,kubeclaw-gateway,kubeclaw-qmd,langgraph-agents}
auth/authelia
collab/{paperless-offsite-backup,zulip-memcached,zulip-rabbitmq}
home/{emqx,netbox}
mcp-system/mcp-gateway-jwt-rotator
media/{immich-offsite-backup,stash}
network/externaldns-cloudflare
observability/{alertmanager,grafana,kube-prometheus-stack-operator,
              kube-state-metrics,prometheus-kube-prometheus-stack}
renovate/renovate-operator + 3 RenovateJob pods
```

(CNPG `postgres-*` pods are operator-managed — separately addressed by
upgrading the cnpg chart or PRing upstream, not by editing values here.)

## Group B — Medium (likely needs testing)

### B1. App writes to rootfs at startup

Likely needs a tmpfs `/tmp` or `/var/cache` mount to satisfy
`readOnlyRootFilesystem: true`. Candidate workloads — each needs a
test cycle:

- `media/jellyfin` (writes transcode cache; check `/cache` mount)
- `media/gonic` (scans library, may write tmp)
- `media/beets` (config + library import)
- `media/romm` (scan state)
- `home/home-assistant` (Python venvs, writes `__pycache__`)
- `home/n8n`, `home/node-red` (Node-style app dirs)
- `home/emqx` (Erlang VM cache)
- `home/esphome-code`, `home/home-assistant-code` (code-server: writes
  to `/home/coder` — already PVC-backed, ROOTFS itself should be
  fine; needs test)
- `ai/{comfyui,khoj,ollama,paperless-ai}` (ML model caches; some are
  already PVC-mounted)
- `collab/{obsidian-couchdb,zulip,open-webui,paperless}`
- `network/wg-easy` (writes config at startup)

### B2. UID 0 in user apps — needs per-app review

32 user-app containers explicitly `runAsUser: 0`. Most are infra-y or
have a structural reason:

- `observability/node-exporter` — needs RAPL (`runAsUser:0` is the
  documented fix; see `project_node_exporter_rapl_root_required`).
  **Keep.**
- `observability/smartctl-exporter` — needs SMART ioctls. **Keep.**
- `home/matter-server` — Matter SDK requires root for bluetooth/IPv6
  multicast. **Likely keep — confirm.**
- `downloads/*/gateway-sidecar` (all 5 *arr + jd2) — pod-gateway
  sidecar runs as root by design. **Keep, document.**
- `collab/zulip` — Zulip image expects root entrypoint that drops to
  `zulip` user internally. **Verify drop-privs is real, then keep.**
- `home/{esphome-code,home-assistant-code}` — code-server images start
  as root then drop. **Verify, then keep.**
- `mcp-system/immich-mcp` — review whether image supports non-root.
- `ai/khoj` — review.
- `media/immichkiosk-transcode` — review (likely needs ffmpeg perms).

## Group C — Hard / accept deviation (document why)

### C1. Storage CSI drivers (rook-ceph, longhorn-system)

**107 privileged + add-caps containers**. CSI node plugins need
`privileged: true` + `SYS_ADMIN` to bind-mount inside the host's mount
namespace. This is inherent to the CSI architecture.

- `rook-ceph` osd, csi-rbdplugin, csi-cephfsplugin — privileged
- `longhorn-system` longhorn-manager, instance-manager, share-manager,
  longhorn-csi-plugin — privileged
- These are operator-managed; remediation = upstream chart change, not
  values override.

**Action**: document as accepted deviation. No PR.

### C2. Network path containers

- `vpn/downloads-gateway-pod-gateway-main` — needs `NET_ADMIN` +
  `NET_RAW` to set up the gluetun tunnel and route table.
- `network/multus` — CNI plugin, needs `NET_ADMIN`.
- `network/wg-easy` — wireguard kernel ops need `NET_ADMIN`. Already
  privileged; could potentially be reduced to `NET_ADMIN` cap-only.
- `observability/blackbox-exporter` — `NET_RAW` for ICMP probes.
- `home/{esphome,node-red}` — `NET_RAW`/`NET_ADMIN` for IoT discovery.

**Action**: accept, document per-app why elevation is necessary.

### C3. Hardware probes

- `observability/{node-exporter,smartctl-exporter}` — root + hostPID
  required for hardware metrics (RAPL energy probes, SMART self-tests).
- `home/{frigate,zigbee2mqtt,zwave-js-ui}` — privileged for direct USB
  device passthrough on the IoT bus.

**Action**: accept, document.

## Top 3 most concerning findings

1. **24 oauth2-proxy HelmReleases with zero securityContext.** These
   sit in front of every authenticated app; if any oauth2-proxy is
   compromised it has unrestricted container capabilities, rootfs
   write, root UID. Easy fix, broad blast radius reduction. Should
   be PR #1.

2. **`network/wg-easy` is privileged** even though it could likely
   run with just `NET_ADMIN` + `NET_RAW`. wg-easy is the only OOB
   access path back into the cluster (see memory: `wg-easy is the
   only OOB access path`); a compromise gets you everything.
   Worth a focused harden-down PR with extra care.

3. **99 user-app containers default to root** (no `runAsUser`, no
   `runAsNonRoot`). The image's `USER` directive saves us most of the
   time, but every one of these is a "trust the upstream image" bet
   that we can convert to an explicit guarantee with one chart-values
   block per app.

## Top 3 intentional deviations to keep

1. **Rook-Ceph + Longhorn CSI plugins** (privileged, SYS_ADMIN).
   Architectural — CSI nodeplugins bind-mount in the host namespace.
   Re-prosecuting this is wasted effort.

2. **`node-exporter` + `smartctl-exporter`** (UID 0, privileged).
   RAPL energy probes are root-only (per memory:
   `node-exporter RAPL needs runAsUser:0`), and smartctl needs ioctls
   no userspace cap exposes.

3. **`vpn/downloads-gateway-pod-gateway-main`** (NET_ADMIN, NET_RAW,
   UID 0 on sidecars). Pod-gateway architecture; sidecars need to
   manipulate the netns. Already isolated to one namespace.

## What this audit doesn't do

- **No remediation in this PR.** Every "easy" finding still needs a
  PR. Don't auto-apply.
- **No Kyverno / PSA admission enforcement.** That's a followup
  decision: once we've remediated the easy + medium tiers, decide
  whether to enforce `restricted` or `baseline` PSA at the
  namespace label level. Until then, ad-hoc compliance only.
- **No drift detection.** Static snapshot. New apps can ship below
  baseline without a check. Enforcement is the answer; this audit
  is the prerequisite for it.

## Methodology

```bash
kubectl get pods -A -o json > all-pods.json

# In-scope: exclude system namespaces
jq -r '.items[]
  | select(.metadata.namespace
      | test("^(kube-system|flux-system|istio-system|cilium-secrets)$")
      | not)' all-pods.json

# Per-container effective securityContext (container overrides pod)
# - eff_runAsUser = container.runAsUser || pod.runAsUser
# - eff_runAsNonRoot = container.runAsNonRoot || pod.runAsNonRoot
# - eff_seccomp = container.securityContext.seccompProfile.type
#                 || pod.securityContext.seccompProfile.type
```

Raw output and per-pod classification are reproducible from
`kubectl get pods -A -o json`; the jq expressions live in this PR's
description.
