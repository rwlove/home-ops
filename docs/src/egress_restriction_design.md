# Egress Restriction Design Proposal

Status: **Proposal — not implemented.** Tier 3 of the network security
roadmap.
Owner: home-ops
Last updated: 2026-05-18

## Problem statement

The 2026-05 NetworkPolicy rollout (`networkpolicy_rollout_plan.md`)
locked down ingress and intra-cluster egress across 19 namespaces, but
the **outbound-to-internet** posture remains coarse. A grep of
`kubernetes/apps/` shows:

- 51 CNP files use `toEntities`.
- **50 of them include `world`** (almost always paired with `:443`).
- Only 12 files use `toFQDNs`, and most of those still fall back to
  `toEntities: [world]` for any canonical (non-CNAME) FQDN per the
  `project_cilium_matchpattern_fqdn_limits.md` workaround.

In effect, every workload that needs **any** outbound HTTPS today has
unrestricted outbound HTTPS. A compromised pod inside the cluster can
exfiltrate to any address on the public internet on 443 (and 465 for
smtp-relay, etc.).

The proximate cause is not laziness — it is `matchPattern`'s silent
failure on K8s-DNS-search-augmented canonical FQDNs. The repo's CNP
authors consistently chose "broad allow that actually works" over
"narrow allow that silently breaks." That tradeoff was correct under
the rollout's time pressure but leaves real exposure.

This document evaluates approaches for closing that gap **without**
re-introducing the silent-failure modes that drove the original choice.

## Goal

Meaningful egress restriction with these constraints:

1. **No regression of the user's verification step.** The 2026-05-18
   full rollback (see `networkpolicy_rollout_plan.md` § "Full rollback
   2026-05-18") established that *real browser-path verification* is
   the only acceptable gate for policy changes. Any egress design
   must be testable from the user's actual workflow, not a simulated
   one.
2. **No new piece of infrastructure to operate.** Single operator,
   home-lab scale. A new always-on proxy pod or sidecar that the
   operator has to monitor, upgrade, and triage is a non-starter
   absent strong justification.
3. **Stays within Cilium primitives where possible.** Cilium 1.19 is
   the cluster's CNI; standing up a parallel egress data plane (e.g.
   a separate proxy mesh) duplicates that investment.
4. **Compatible with GitOps + per-app PR rhythm.** Whatever ships
   should be expressible in `kubernetes/components/` or per-app
   overlays, not require out-of-band cluster state.
5. **The L7 DNS proxy in `network-policy/baseline/allow-dns` stays
   intact.** It is load-bearing for every FQDN egress rule and for
   Cilium's FQDN cache; removal silently breaks all toFQDNs.

## Current egress inventory

Grouped from the 51 CNPs by destination shape:

| Category | Example apps | Today's allow shape | Tractability |
|---|---|---|---|
| Package registries — CNAME-fronted | actions-runner-system (github.com, *.githubusercontent.com), media/recyclarr | `matchPattern: <fqdn>` works | High (already narrow) |
| Provider APIs — canonical | external-secrets→1P Connect cloud, smtp-relay→mailgun, langgraph→Anthropic/Pushover, cert-manager→ACME, ai/khoj→HuggingFace | `matchPattern + .*` workaround OR `world:443` | Low (matchPattern silently fails on canonical) |
| Container registries — pull-through via ZOT | n/a (in-cluster ZOT) | covered by `toEndpoints` | Already restricted |
| S3 to AWS (offsite backups) | immich, paperless rclone CronJobs | `world:443` | Medium (AWS S3 IP ranges are published but rotate) |
| LAN-internal hosts (host identity) | longhorn→beast NFS, snmp/omada→LAN devices | `toEntities: [host, remote-node, world]` mix | Already non-internet but `world` is the catch-all |
| Truly broad / user-driven | home-assistant, esphome, n8n, node-red, searxng, glance, glance-user, open-webui, runners | `world:443` (sometimes + 80) | Inherently broad |
| ESPHome compile / runners arbitrary code | esphome/code, actions-runner-system/runners | `world` + `matchPattern: "*"` | Inherently broad |

**Distinct legitimate external destinations** (deduped across all apps):
roughly 25-30 FQDN families. The bulk of `world:443` rules collapse to
~6 categories: GitHub/GHCR, HuggingFace + R2, 1Password (cloud + Connect),
Cloudflare API, ACME endpoints, Mailgun, Pushover, Anthropic. The
rest — home-assistant, esphome, searxng, n8n, runners, ESPHome
compile, recipe scrapers, etc. — are *inherently* broad because the
operator-installable surface is user-driven.

So the cluster has two qualitatively different egress populations:

- **Bounded apps** (a known FQDN list, even if Cilium can't narrow
  it today) — ~70% of the CNPs by file count.
- **Inherently unbounded apps** — home, automation, dashboards,
  user-driven aggregators, code-execution sandboxes. ~30%.

A meaningful design must address the bounded population while leaving
the unbounded population workable.

## Approaches considered

### A. `toCIDR` lists per service

Maintain `toCIDR` allowlists per app: `api.cloudflare.com` → maintained
list of CF anycast prefixes, `acme-v02.api.letsencrypt.org` → ACME's
published ranges, etc.

**Pro:** Bypasses matchPattern entirely. Cilium handles CIDR matching
reliably (when not against host-net identities — see
`project_cilium_ipblock_apiserver.md`, but kube-apiserver isn't the
target here). Static, GitOps-able, auditable.

**Con:** Maintenance burden is the killer. Cloudflare's IP ranges
rotate. Anthropic uses AWS/GCP. HuggingFace fronts via CloudFront.
1Password Connect's cloud endpoint is on AWS. The provider lists either
get out of date (silent breakage on rotation) or require a
Renovate-style update bot pointed at upstream IP-range manifests. None
of the providers we'd target publish those manifests in a
machine-readable form we already consume.

**Verdict:** Discard. The maintenance signal-to-noise ratio is worse
than today's `world:443` — instead of "broad allow that works," we get
"narrow allow that silently breaks on provider IP rotation," which is
exactly the failure mode that drove the original `world:443` choice.

### B. Cluster-wide HTTP CONNECT proxy

Stand up a forwarding HTTPS proxy (e.g. Squid, Envoy, or a
purpose-built one) in a dedicated namespace. Every app's egress CNP
allows only `toEndpoints: <proxy>`. The proxy enforces an FQDN
allowlist at L7, and pods configure `HTTPS_PROXY=http://proxy:3128`.

**Pro:** Centralizes the allowlist. L7 FQDN matching at the proxy is
not subject to Cilium's K8s-DNS-search-augmentation quirk — Squid sees
the literal CONNECT host header. Cleanly auditable: one log stream
shows every external request the cluster makes.

**Con:** Violates constraint #2 (no new infrastructure). It's a new
SPOF in the egress path; if the proxy pod crashloops or its allowlist
ConfigMap has a typo, **every** internet-needing workload in the
cluster breaks simultaneously. Also: `HTTPS_PROXY` env var support is
inconsistent — many apps respect it (curl, Python requests, npm); many
don't (Go's `net/http` only if explicitly configured, some Java HTTP
clients, anything statically built against a non-proxy-aware library).
Apps that ignore the proxy bypass it entirely, recreating today's
posture silently. Some apps (esphome compile, runners) intentionally
fan out to arbitrary destinations and would need bypass rules
*anyway*.

**Verdict:** Discard. Operating a new always-on proxy and the
allowlist-as-config story for a single-operator cluster is a step
function in operational complexity, and the bypass-via-non-proxy-aware
clients gap reproduces the same silent-failure mode we're trying to
escape.

### C. DNS-based with longer cache TTL + accept matchPattern caveats

Keep `toFQDNs` as the primary mechanism. Configure Cilium's DNS proxy
with longer minimum TTLs (so the FQDN→IP cache survives provider DNS
TTL jitter). Accept that matchPattern will continue to fail on
canonical FQDNs and audit-fix per app when it surfaces.

**Pro:** No new infrastructure. Leverages existing primitives.

**Con:** This is the status quo we're trying to escape. The
`project_cilium_matchpattern_fqdn_limits.md` failure mode is not about
cache lifetime — it's about the pattern matcher not seeing the
augmented FQDN. Longer TTL doesn't fix it. Continuing the per-app
audit-and-widen-to-world cycle is what got us to "50 of 51 use world."

**Verdict:** Discard. Doesn't move the posture forward.

### D. Cilium 1.20+ FQDN improvements

Cilium 1.20 (released 2025-Q4) shipped improvements to the FQDN proxy,
including better handling of DNS search domain expansion and the new
`--dns-proxy-enable-transparent-mode` option that intercepts DNS at
the eBPF layer rather than via iptables redirect. The 1.21 line
(in-development, target 2026-Q2) adds explicit support for
`matchName` against canonical FQDNs (release-notes commit
`cilium/cilium#36418`, the upstream fix for the exact failure mode
documented in `project_cilium_matchpattern_fqdn_limits.md`).

**Pro:** Fixes the root cause of why we fell back to `world:443`. Same
data plane, no new infrastructure. CNPs stay in the same shape; we
just stop hitting the silent-failure trap.

**Con:** Requires a Cilium upgrade (we're on 1.19.4). 1.19 → 1.20 is
not a config-only bump — it touches every node's CNI agent and a few
agent-config defaults changed. Risk is non-trivial in a one-operator
cluster. 1.21 isn't GA yet, so the *real* matchName-canonical fix is
~6+ months out. In the interim, 1.20 alone doesn't close the gap; it
just makes the matchPattern hack more reliable for the apps that
were already on it.

**Verdict:** Hold. Track upstream; revisit when 1.21 reaches stable.
The Cilium upgrade is the right cluster work to plan, but as a
separate workstream — it shouldn't gate this design.

### E. Hybrid: keep `world:443`, layer Hubble alerting for unusual destinations

Leave the CNP posture as-is. Use Hubble's flow data — already exported
to the metrics pipeline — to detect *unusual* egress destinations per
app. Define a baseline (the FQDNs each app legitimately reaches over
~7 days) and alert when a flow's destination falls outside that
baseline.

**Pro:** Detection-not-prevention is the right primitive when
prevention has been demonstrated to silently fail. No new
infrastructure (Hubble + kube-prometheus-stack + Alertmanager are
already running; Alertmanager → ntfy/Pushover routing exists). Catches
the exfiltration scenario the threat model actually cares about
(compromised pod talks to attacker-controlled C2) without breaking
legitimate broad-egress apps. Reusable across the bounded *and*
unbounded populations. Failure mode is "alert noise" rather than "app
silently down" — the operationally-safer direction.

**Con:** Doesn't *prevent* exfiltration to an unknown destination; the
attacker gets one trip out before the alert fires. The "baseline" has
to be built and maintained (per-app FQDN sets drift as configs
change). Alert-fatigue risk if the baseline isn't tight — searxng,
home-assistant, n8n will routinely surface novel destinations.

**Verdict:** **Adopt as primary.** Detection plus per-app narrowing
(approach F below) where genuinely tractable.

### F. Narrow what's narrow-able; leave the rest broad

A focused, targeted second pass on the existing CNPs:

- For apps with a **stable, finite, documented** external FQDN set
  (the bounded population): convert `world:443` to `toFQDNs:
  matchName: <fqdn>` per FQDN, using the paired `matchPattern:
  foo.com` and `matchPattern: foo.com.*` workaround for canonical
  FQDNs. Already the pattern in actions-runner-controller,
  recyclarr, pump-cv, github-mcp, paperless-ai.
- For apps with **inherently unbounded** egress (home-assistant,
  esphome, n8n, node-red, searxng, glance, glance-user, open-webui,
  runners, esphome/code, home-assistant/code): leave `world:443` and
  document the design decision in a per-CNP comment.
- For apps that fall in between (e.g. ai/ollama, ai/comfyui pulling
  models from huggingface + R2 + pypi): keep `world:443` but tighten
  the *port set* — drop port 80 if it isn't actively used (most apps
  don't need it).

**Pro:** Concrete, incremental, no infrastructure cost. Each app's
narrowing is one PR with per-app browser-path verification (the gate
established in the 2026-05-18 rollback retrospective).

**Con:** Doesn't reduce the count of `world:443` rules much — the
unbounded population is structurally broad. Most of the value comes
from the bounded apps that *already* use `matchPattern` correctly.
The "audit and narrow" pass is real work for modest CNP-count
reduction.

**Verdict:** **Adopt as secondary**, paired with E. Approach E
provides the meaningful security gain; approach F is documentation
hygiene that surfaces design intent in each CNP.

### G. Perimeter (brain firewalld) egress filtering

The cluster's actual internet egress all routes through `brain`
(192.168.6.1, the home router). brain runs firewalld and already has
the OOB SSH pinhole from the 2026-05-14 work. Apply L4 egress filtering
at brain for known-bad destinations (geo-blocked countries, known C2
prefixes from threat-intel feeds) or known-good allowlists per source
host.

**Pro:** Filtering at the perimeter is a defense-in-depth layer that
catches cluster-side bypass scenarios (a compromised CNI agent that
ignores CNPs, a misconfigured pod-gateway leak). Single chokepoint to
maintain. brain config is already version-controlled in
`rwlove/lovenet-network-configuration`.

**Con:** brain firewalld can't see into Cilium's per-pod identity —
every cluster-egress flow looks like "Kubernetes node IP → external
IP" from brain's perspective. The granularity is per-node, not
per-pod or per-app. So perimeter filtering can do "drop traffic to
known-malicious prefixes" but not "allow comfyui to reach
huggingface but not anywhere else." Different threat model, different
control. Doesn't address the per-pod compromise scenario this
roadmap is targeting.

**Verdict:** Out of scope here, in-scope for a different roadmap
tier. The right layer for threat-intel-feed blocking, but not for
per-app egress restriction.

## Recommendation

**Adopt E + F as a paired strategy.** Detection-first via Hubble
alerting, with opportunistic per-app narrowing where the FQDN set is
known-stable.

Rationale tied to the cluster's constraints:

1. **One operator on call.** Prevention controls that silently fail
   create user-discovered outages (the 2026-05-18 rollback proved
   this). Detection controls fail in a noisier, more recoverable
   direction (alert fires, operator investigates, no app is broken).
2. **GitOps + per-app PR rhythm.** Both halves fit. The Hubble
   baselining is a one-shot dashboard + alert-rule PR. Per-app
   narrowing is one CNP PR per app with the established
   browser-verification gate.
3. **No new infrastructure.** Hubble, kube-prometheus-stack,
   Alertmanager, and the Pushover Provider are all already running.
   No new pod, no new mesh, no new control plane.
4. **Compatible with the eventual Cilium 1.21 upgrade.** When the
   matchName-canonical fix lands upstream, this design's "narrow
   what's narrow-able" pass converges naturally — the
   `matchPattern + .*` hack collapses to clean `matchName` lines and
   `world:443` rules drop further. The Hubble alerting stays
   useful regardless.
5. **Threat-model-aligned.** The realistic threat we're hardening
   against is a compromised app phoning home or exfiltrating
   credentials — not a sophisticated CNI-evading rootkit (where the
   right control is at the host or perimeter, see approach G).
   Detection catches the realistic case; we're not pretending to
   solve the harder one with CNP changes.

## Phased rollout sketch

### Phase 0 — Baseline measurement (1-2 weeks, passive)

Build the Hubble destination baseline. No CNP changes. Two
deliverables:

1. **Per-app egress-FQDN Grafana panel.** Source: Hubble flow data
   already in the metrics pipeline. Group by `source_pod_namespace +
   source_pod_labels.app.kubernetes.io/name → destination_fqdn`. Show
   the unique FQDN set per app, with first-seen and last-seen
   timestamps. Use the existing observability stack — no new
   exporters.
2. **Per-app baseline JSON exported to git.** Once the panel settles
   (≥7 days of data), snapshot the per-app FQDN sets into a
   `kubernetes/components/network-policy/egress-baselines/` directory
   (one file per app, FQDN list). This is the *expected egress*
   manifest, version-controlled so changes are reviewable.

**Success criterion:** Baseline captured for every app currently using
`toEntities: [world]`, with no false-positive surprises (e.g. apps
talking to FQDNs that the operator didn't know about — investigate
each before declaring baseline).

### Phase 1 — Single-app pilot: detection alerting (1 week)

Pick **one bounded-population app** with a stable, well-understood
FQDN set. Recommendation: **media/recyclarr** — already on
`matchPattern + .*`, low blast radius, no real-time user dependency,
flow volume is bounded (it scrapes TRaSH-Guides on a schedule).

Steps:

1. PR: add a Prometheus recording rule that counts per-pod egress
   flows to destinations *outside* the recyclarr baseline JSON.
2. PR: add an Alertmanager rule that fires (`severity=warning`,
   routed to Pushover) when that count is non-zero over a 5-minute
   window.
3. **Wait 7 days.** Alert should never fire on legitimate recyclarr
   behavior. If it does, investigate — either the baseline missed
   something legitimate (update the baseline) or the app is doing
   something unexpected (investigate further).

**Success criterion:** 7-day quiet period. No false-positive alerts.
Operator gains confidence the detection mechanic works.

### Phase 2 — Bounded-app rollout (4-6 weeks, one app per PR)

Apply the same detection pattern to the rest of the bounded
population, one app per PR, in order of lowest-blast-radius first.
Candidate order:

1. recyclarr (Phase 1 pilot)
2. external-secrets→1P Connect cloud
3. cert-manager→ACME
4. actions-runner-controller operator (not the runners — runners are
   unbounded)
5. mcp-system/github-mcp
6. mcp-system/immich-mcp
7. media/lidarr, media/sonarr, media/radarr (per-app, sequential)
8. observability/* exporters

Each PR: one new alert rule + the per-app baseline JSON. No CNP
changes.

In parallel, opportunistic approach-F PRs where an app's `world:443`
rule can collapse to a small `matchPattern` list with confidence —
but only when the operator has bandwidth to do the
browser-verification step on each.

### Phase 3 — Unbounded-app posture (1 week, design + documentation)

For the unbounded population (home-assistant, esphome, n8n, node-red,
searxng, glance, glance-user, open-webui, runners, /code variants):

1. **Don't try to baseline.** The baseline would churn constantly
   and generate alert fatigue.
2. **Document the design decision** in a comment block in each CNP:
   "Unbounded egress is intentional — see
   `docs/src/egress_restriction_design.md` § Unbounded apps." This
   makes the broad allow legible as design rather than oversight.
3. **Consider a coarser alert** (per-app egress-volume anomaly:
   "home-assistant just sent 100x its 24h rolling-average egress
   bytes"). Catches the bulk-exfil scenario without needing an
   FQDN allowlist. Cheap to implement; tune the threshold over time.

### Phase 4 — Convergence with Cilium upgrade (when 1.21 lands)

When Cilium 1.21 ships with the matchName-canonical fix:

1. Cilium upgrade as its own workstream (not in this roadmap).
2. Once upgraded, sweep the `matchPattern + .*` hack across all
   CNPs back to clean `matchName: <fqdn>`. Mechanical change, one
   PR per namespace, reuse the existing PR-per-namespace pattern.
3. Re-evaluate which `world:443` rules can collapse to narrow
   `toFQDNs` rules now that matchName works on canonical FQDNs.
4. Keep the Hubble detection layer regardless — it catches
   exfiltration scenarios that CNP narrowing cannot.

## What this design explicitly does not do

- **Does not** stand up a forwarding proxy (approach B).
- **Does not** introduce a maintained CIDR allowlist for provider
  endpoints (approach A).
- **Does not** propose a Cilium upgrade as part of this work
  (approach D). Track upstream, plan separately.
- **Does not** touch brain firewalld for cluster-egress filtering
  (approach G). That's a different control at a different layer for
  a different threat.
- **Does not** add a new piece of infrastructure for the operator
  to maintain — only new alert rules + version-controlled baseline
  manifests, both within the existing observability stack.

## References

- `docs/src/networkpolicy_rollout_plan.md` — the underlying CNP
  rollout, including the 2026-05-18 full-rollback retrospective.
- `project_cilium_matchpattern_fqdn_limits.md` — why matchPattern
  silently fails on canonical FQDNs and why we ended up with
  `world:443` everywhere.
- `project_cilium_l4_port_targetport.md` — the socket-lb-rewrite
  gotcha that breaks naive Pattern A overlays.
- `feedback_netpol_rollout_needs_per_app_browser_test.md` — the
  verification-gate decision that constrains any future netpol
  change.
- `project_cilium_ipblock_apiserver.md` — why `toCIDR: 0.0.0.0/0`
  doesn't work for some destinations; relevant context for why
  approach A is fragile beyond just IP-rotation.
