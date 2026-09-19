# Observability Runbook

Day-2 operator reference for the observability stack: Prometheus +
Alertmanager (`kube-prometheus-stack`), Grafana, Loki, Tempo, Vector,
the OpenTelemetry Collector, and `kube-state-metrics`. Covers scrape
authoring, alert routing/silencing, log and trace lookup, and the
common failure shapes for each. Manifests live under
`kubernetes/apps/observability/`.

For general pod/Flux/Ceph/Longhorn triage see [`debugging.md`](debugging.md).
For the MCP fleet's own metrics see [`mcp_observability.md`](mcp_observability.md).
For named recurring failure modes with a documented root cause see
[`failure_mode_runbooks.md`](failure_mode_runbooks.md).

## Stack map

| Component | Role | Manifests |
|---|---|---|
| `kube-prometheus-stack` | Prometheus, Alertmanager, the Prometheus Operator CRDs | `kubernetes/apps/observability/kube-prometheus-stack/` |
| Grafana | Dashboards, datasource-backed queries | `kubernetes/apps/observability/grafana/` |
| Loki | Log storage (Monolithic mode, Garage S3 backend) | `kubernetes/apps/observability/loki/` |
| Tempo | Trace storage (single-binary, `ceph-block` local backend) | `kubernetes/apps/observability/tempo/` |
| Vector (agent + aggregator) | Log shipping: node → aggregator → Loki | `kubernetes/apps/observability/vector/` |
| OpenTelemetry Collector | OTLP trace ingestion → Tempo | `kubernetes/apps/observability/opentelemetry-collector/` |
| `kube-state-metrics` | Cluster object-state metrics (pods, deployments, PVCs, …) | `kubernetes/apps/observability/kube-state-metrics/` |
| `silence-operator` | GitOps-managed Alertmanager silences | `kubernetes/apps/observability/silence-operator/` |
| `blackbox-exporter` | ICMP / TCP / HTTP / custom-module probes | `kubernetes/apps/observability/exporters/blackbox-exporter/` |

Retention and durability, for reference when triaging "why is this data
gone":

| Store | Retention | Backend |
|---|---|---|
| Prometheus | 14d or 100GB, whichever hits first | local `ceph-block` PVC |
| Loki | 30d (`retention_period: 30d`) | Garage S3 (`loki-chunks-v1` bucket) |
| Tempo | 14d (`336h`) | local `ceph-block` PVC |

Prometheus retention shrinking or Loki retention shrinking are both
**propose-only** changes per the observability prime directive — they
remove the historical baseline an in-flight investigation needs. Don't
drop either without a documented reason and a rollback path.

## Scrape config authoring

Four CRDs cover scrape target discovery; pick based on what's being
scraped, not habit:

| CRD | Use for |
|---|---|
| `ServiceMonitor` | A Service backed by pods exposing `/metrics` |
| `PodMonitor` | Pods with no fronting Service (or you need pod-level labels) |
| `Probe` | Blackbox-style checks — ICMP reachability, HTTP 2xx, TCP connect, or a custom blackbox module hitting an app endpoint |
| `ScrapeConfig` | Static or file-based targets outside the pod/service model (e.g. bare-metal exporters) |

The HelmRelease sets `podMonitorSelectorNilUsesHelmValues: false`,
`probeSelectorNilUsesHelmValues: false`, `scrapeConfigSelectorNilUsesHelmValues: false`,
and `serviceMonitorSelectorNilUsesHelmValues: false` — Prometheus picks
up **every** CR of these kinds cluster-wide, no label-matching dance
required. A new CR anywhere in the tree is scraped automatically once
Flux reconciles it; you don't need to touch the `kube-prometheus-stack`
HelmRelease itself to add a target.

### ServiceMonitor example

```yaml
---
# yaml-language-server: $schema=https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/monitoring.coreos.com/servicemonitor_v1.json
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: <app>
  labels:
    app.kubernetes.io/name: <app>
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: <app>
  endpoints:
    - port: http
      path: /metrics
      interval: 30s
      scrapeTimeout: 10s
```

`selector` matches the **Service's** labels, not the pod's — a
ServiceMonitor with a selector that matches nothing produces zero
targets with no error anywhere. That's the single most common cause of
"I added a ServiceMonitor and Grafana still shows no data."

### Probe example (blackbox)

```yaml
---
# yaml-language-server: $schema=https://k8s-schemas.home-operations.com/monitoring.coreos.com/probe_v1.json
apiVersion: monitoring.coreos.com/v1
kind: Probe
metadata:
  name: <target-group>
spec:
  interval: 2m
  module: icmp   # or http_2xx, tcp_connect, or a custom module (see blackbox-exporter helmrelease.yaml)
  prober:
    url: blackbox-exporter.observability.svc.cluster.local:9115
  targets:
    staticConfig:
      static:
        - <hostname-or-url>
```

Pair `interval` with any downstream `for:` clause deliberately — a
15m-`for:` alert gains nothing from a 30s probe interval, and a too-slow
interval delays detection past what the alert's `for:` assumes. See
`kubernetes/apps/observability/exporters/blackbox-exporter/app/probes.yaml`
for the live pairing conventions (interval vs `scrapeTimeout` vs the
alert's `for:`), including the "devices" / "devices-sleepy" split for
targets with legitimate radio/power-save downtime.

### ScrapeConfig example (static, non-k8s target)

```yaml
---
# yaml-language-server: $schema=https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/monitoring.coreos.com/scrapeconfig_v1alpha1.json
apiVersion: monitoring.coreos.com/v1alpha1
kind: ScrapeConfig
metadata:
  name: <target>
spec:
  staticConfigs:
    - targets: ["<host>:<port>"]
      labels:
        job: <target>
  metricsPath: /metrics
```

See `kubernetes/apps/observability/kube-prometheus-stack/addons/scrapeconfigs/`
for live examples (bare-metal node-exporter on hosts outside the
kubelet's reach, an SMTP relay, Home Assistant).

### Why isn't my target being scraped?

```sh
# List every discovered target and its health
kubectl -n observability port-forward svc/kube-prometheus-stack-prometheus 9090:9090
# then browse http://localhost:9090/targets, or:
prom_execute_query 'up{job="<job>"}'
```

Triage order:

1. **Does the ServiceMonitor/PodMonitor selector actually match the
   Service/pod labels?**

   ```sh
   kubectl -n <ns> get servicemonitor <name> -o jsonpath='{.spec.selector.matchLabels}'
   kubectl -n <ns> get svc <svc> --show-labels
   ```

   A mismatch here means the target never appears in Prometheus at
   all — not "down," just absent. `prom_execute_query 'up{job=~".*<app>.*"}'`
   returning nothing (not `0`, nothing) is the signature.

2. **Is the CR's namespace covered by `namespaceSelector`?** Most
   ServiceMonitors in this repo omit `namespaceSelector` (defaults to
   same-namespace), which is correct for co-located metrics.

3. **Does the target respond on the declared port/path?**

   ```sh
   kubectl -n <ns> exec <pod> -- wget -qO- http://localhost:<port>/metrics | head
   ```

   If this 404s or connects but returns HTML, the `path` in the
   ServiceMonitor is wrong or the app doesn't expose metrics on that
   port.

4. **CNP egress from Prometheus, or ingress on the target?** Prometheus
   scrapes cross a `CiliumNetworkPolicy` boundary for every namespace it
   reaches into. If the target namespace has a default-deny ingress
   policy, it needs an explicit allow for the Prometheus pod — see
   `kubernetes/apps/observability/kube-prometheus-stack/app/cnp-allow.yaml`
   for the egress side and any target app's own `cnp-allow.yaml` for the
   matching ingress. A missing ingress-side allow is a **silent** drop —
   `up == 0`, not a Cilium-visible policy-deny in the target's own
   metrics (the packet never reaches the app).

5. **`up{job="..."} == 0` with a real connection failure** (target
   discovered, scrape fails). Check the target pod is `Running`+`Ready`
   and its own logs for a crash or panic under load.

## Alert routing

Routing is entirely by **alertname**, not by severity — see
`kubernetes/apps/observability/kube-prometheus-stack/app/alertmanagerconfig.yaml`.
Two alertnames route to a `null` receiver (`Watchdog`, `InfoInhibitor`,
plus `KubePodPendingBrief` — deliberately Grafana-only, see the comment
in that file for why an `inhibitRule` can't do this instead); every
other alert — critical, warning, or info — falls through to the single
`pushover` receiver and notifies. There is no severity-based
suppression today: if you want an alert to page, no label change is
needed; if you want one silenced, it needs an explicit `null`-receiver
route by alertname.

The one severity-aware behavior is an `inhibitRule`: a firing
`critical` suppresses a `warning` sharing the same `alertname` +
`namespace`. This is the flood/mute tradeoff already made in this
config — it exists to stop a critical+warning pair on the same
condition from double-paging, not to hide unrelated warnings.

```sh
# Current Alertmanager routing state
kubectl -n observability port-forward svc/kube-prometheus-stack-alertmanager 9093:9093
# then browse http://localhost:9093, or query via Prometheus:
prom_execute_query 'ALERTS{alertstate="firing"}'

# Which receiver would a given alert route to (dry-run, no state change)
# — use the Alertmanager UI's routing tree view, not a live send.
```

### Adding a new alerting rule

Minimum shape for a new alert:

```yaml
---
# yaml-language-server: $schema=https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/monitoring.coreos.com/prometheusrule_v1.json
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: <app>-rules
spec:
  groups:
    - name: <app>.rules
      rules:
        - alert: <AppSomethingWrong>
          expr: <promql> > <threshold>
          for: 5m
          labels:
            severity: warning   # or critical
          annotations:
            summary: <one-line, includes {{ $labels.* }} for context>
            description: <what it means, what to check first>
```

Rules for the `for:` clause:

- **Never ship `for: 0m` (or omit `for:`) on a metric that can
  transient** — pod restart, brief network blip, a scrape miss. That's
  the textbook flap generator: it fires and clears within the scrape
  interval and trains the recipient to swipe the Pushover notification
  away without reading it.
- **5m is the floor** for anything keyed on pod/target state
  (`up == 0`, `kube_pod_status_phase`). This repo's own rules land
  between 2m (things that resolve fast on their own, kept as
  `warning`) and 30m (steal, CPU load — things that need to be
  sustained to matter). See `kubernetes/apps/observability/kube-prometheus-stack/addons/alerts/node-exporter.yaml`
  for the reference set.
- **Target-down alerts use `absent(up{job=~"..."} == 1)` or
  `up{job="..."} == 0`**, `for: 5m`–`15m` depending on how tolerant the
  target is of restarts. See
  `kubernetes/apps/observability/exporters/omada-exporter/app/prometheusrule.yaml`
  and `kubernetes/apps/observability/exporters/snmp-exporter/app/apc-ups/prometheusrule.yaml`
  for two live examples, including the comment threads on *why* a
  target-specific rule was added on top of the generic upstream
  `TargetDown` alert (faster, named detection vs. a 22-hour-unnoticed
  generic page).
- **Route new critical alerts to `pushover` by doing nothing** — that's
  the default receiver. Only touch `alertmanagerconfig.yaml` if you
  need to *suppress* an alertname, and that's a propose-only change
  (see below).

### Silences vs. suppression routes — pick the narrower tool

Two ways to make an alert stop paging, with very different blast radius:

| Need | Tool | Scope | Reversal |
|---|---|---|---|
| "This alert is expected noise for a specific target, permanently" (e.g. one known-bursty NVMe device, one specific OSD) | A `Silence` CR via `silence-operator` | One alertname + specific label matchers | Delete the `Silence` CR |
| "I'm doing maintenance for the next N hours" | A `Silence` CR with a defined end, OR (if the operator isn't present) `amtool silence add` directly against Alertmanager | Time-bounded, scoped to the maintenance | Silence expires on its own |
| "This whole class of alert should never page" | Route change in `alertmanagerconfig.yaml` | Every instance of that alertname, forever, cluster-wide | Requires restoring the AlertmanagerConfig — **propose-only** |

`silence-operator` reconciles `Silence` CRs (`observability.giantswarm.io/v1alpha2`)
into Alertmanager every hour (`interval: 1h` on its HelmRelease) — a
CR you commit doesn't take effect instantly; force it if you need it
live now:

```sh
kubectl -n observability rollout restart deployment silence-operator
```

Example — narrow, permanent silence for one specific noisy label
combination (see `kubernetes/apps/observability/silence-operator/silences/ceph.yaml`
for the live version):

```yaml
---
# yaml-language-server: $schema=https://k8s-schemas.home-operations.com/observability.giantswarm.io/silence_v1alpha2.json
apiVersion: observability.giantswarm.io/v1alpha2
kind: Silence
metadata:
  name: <descriptive-name>
spec:
  matchers:
    - name: alertname
      value: <AlertName>
    - name: <label>
      value: "<specific-value>"
```

**A `Silence` CR with no time bound is a standing exception, not a
maintenance window.** The `silence-operator` silences in this repo
(`kubernetes/apps/observability/silence-operator/silences/`) are all
of the first kind above — permanent, narrow, one label-set — not
maintenance windows. For an actual maintenance window, set a real
start/end and remove the CR (or let it lapse) when the window closes;
don't leave a "while we figure it out" silence in the tree with no
expiry — that's exactly the kind of change this repo's observability
persona treats as propose-only if it's broader or longer than the
actual work.

### "Is this alert flapping, or did I bury a real one?"

Symptoms and how to tell them apart:

- **Flapping**: the same alertname fires and resolves repeatedly within
  a short window, each cycle shorter than a human reasonably reacts to.
  Confirm with:

  ```promql
  changes(ALERTS_FOR_STATE{alertname="<Name>"}[1h])
  ```

  A high change-count with a short mean time-firing is flap. The fix is
  almost always a longer `for:` clause or a threshold that better
  separates signal from noise — not suppression. Suppressing a flapping
  alert without fixing the underlying `for:`/threshold just delays the
  same problem to the next person who forgets it's silenced.
- **Buried**: an alert is *not* flapping — it fired once, stayed firing,
  and nobody saw it, because a `null` route, an overly broad
  `inhibitRule`, or a stale silence swallowed it. Confirm with:

  ```promql
  ALERTS{alertstate="firing"}
  ```

  cross-referenced against what actually reached Pushover. If
  `ALERTS` shows firing but Pushover has nothing, walk the routing tree
  (`alertmanagerconfig.yaml` routes, then any active `Silence` CRs) —
  don't assume it's a delivery failure until routing is ruled out.

  ```sh
  kubectl get silences.observability.giantswarm.io -A
  # or against Alertmanager directly:
  kubectl -n observability port-forward svc/kube-prometheus-stack-alertmanager 9093:9093
  # browse http://localhost:9093/#/silences
  ```

The prime directive for this stack cuts one way on this dichotomy:
never trade a buried-alert risk for a flap-reduction win without
naming the tradeoff first. A `for:` clause bump that's too aggressive
(say, 30m on something that matters within 5m) trades flap-reduction
for muting a real incident — check what the alert is actually
protecting against before lengthening its `for:`.

## Loki (logs)

Log path: node-level `vector` agents (DaemonSet) tail container logs
→ ship to the `vector` aggregator (`vector-aggregator-app` Service,
`:6000`) → aggregator writes to Loki over the `loki` HTTP sink
(`:3100`). See `kubernetes/apps/observability/vector/agent/resources/vector.yaml`
and `.../vector/aggregator/resources/vector.yaml` for the full
source/transform/sink pipeline, including the one active
transform (a filter that drops a known-benign readiness-probe log line
at the aggregator rather than weakening the probe itself — a good
template for handling other single-line log noise).

Loki indexed labels (from the aggregator's `loki_kubernetes` sink):
`app`, `namespace`, `node`. Everything else lives in the log line body
— query it with LogQL's line filters or `| json` / `| logfmt` parsing,
not as a label (Loki's cardinality model punishes high-cardinality
labels hard).

```logql
# Recent logs for an app, tailed
{app="<app>"} |= ""

# Errors only, last hour
{namespace="<ns>", app="<app>"} |~ "(?i)error|panic|fatal"

# Rate of log lines per app, for spotting a noisy or silent app
sum by (app) (rate({namespace="<ns>"}[5m]))
```

Query via the Grafana Explore view (Loki datasource) or `logcli`
against `http://loki.observability.svc.cluster.local:3100` from inside
the cluster.

### Loki ingestion stalled

```sh
kubectl -n observability get pods -l app.kubernetes.io/name=loki
kubectl -n observability logs -l app.kubernetes.io/name=loki --tail=200
```

Loki ships its own alerting rules (`kubernetes/apps/observability/loki/app/prometheus-rule.yaml`):
`LokiRequestErrors` (>10% 5xx over 5m, `for: 15m`), `LokiRequestPanics`
(any panic, no `for:` — a panic is never transient-noise), and
`LokiRequestLatency` (p99 >1s, `for: 15m`, excludes tail routes). If
none of those are firing but Grafana shows no log data:

1. Check the aggregator is actually receiving from agents — the agent
   sink is a `vector`-native TCP sink to `vector-aggregator-app:6000`,
   not HTTP; a `CiliumNetworkPolicy` gap here fails silently the same
   way a Prometheus scrape gap does.
2. Check `configmap.reloader.stakater.com/reload` annotations reloaded
   after any bucket-credential rotation — Loki's `podAnnotations`
   depend on the `loki-chunks-bucket-v1` ConfigMap/Secret hash to
   trigger a restart on credential change.
3. Confirm the Garage bucket is reachable — Loki's storage backend is
   S3 (Garage), not local disk; a Garage outage stalls both ingestion
   and any query touching non-cached chunks. See the storage operator's
   Garage runbook coverage for that side.

## Tempo (traces)

Single-binary Tempo, local `ceph-block` storage (deliberately not
Garage — avoids Garage's own traces depending on Garage, a circular
observability dependency). Two ingestion paths, both OTLP:

- Apps → OpenTelemetry Collector (`otlp/tempo` exporter,
  `tempo.observability.svc.cluster.local:4317`) — the standard path,
  gets `k8s_attributes` enrichment (namespace, pod name, etc.) via the
  collector's `kubernetesAttributes` preset.
- Apps → Tempo directly (`tempo` Service `:4317`/`:4318`) — a
  skip-collector shortcut for early debugging of a single
  OTel-instrumented app; loses the k8s-attribute enrichment.

```sh
# Collector health (traces-only pipeline: otlp -> memory_limiter -> k8s_attributes -> batch -> otlp/tempo)
kubectl -n observability logs -l app.kubernetes.io/name=opentelemetry-collector --tail=100

# Tempo health
kubectl -n observability get pods -l app.kubernetes.io/name=tempo
kubectl -n observability logs -l app.kubernetes.io/name=tempo --tail=200
```

Query traces via Grafana Explore (Tempo datasource) — search by
`k8s.namespace.name` / `k8s.pod.name` attribute, or by trace ID if
propagated from a log line. `metricsGenerator` is off (no
trace-derived Prometheus metrics yet) — if a dashboard needs
span-derived RED metrics, that's a deliberate follow-up, not something
already available.

### Trace ingestion stalled

1. Confirm the app is actually emitting OTLP — most silent-trace issues
   are the app never calling `otlp/tempo` (wrong endpoint, missing SDK
   config) rather than a broken pipeline.
2. Check the collector's `memory_limiter` isn't dropping — `check_interval: 1s`,
   `limit_percentage: 75`, `spike_limit_percentage: 25` against 256Mi
   request / 1Gi limit; a `memorylimiterprocessor` refusal shows up in
   collector logs as `data refused due to high memory usage`.
3. Confirm the CNP allows the app's egress to the collector's OTLP
   ports (`4317` gRPC / `4318` HTTP) — same silent-drop failure mode as
   Prometheus scrapes and Loki shipping.

## kube-state-metrics

Feeds `kube_pod_status_phase`, `kube_pod_container_status_*`,
`kube_persistentvolumeclaim_*`, and similar object-state metrics that
back most of the custom PrometheusRules in this repo (pod-pending,
PV/PVC, OOMKilled — see
`kubernetes/apps/observability/kube-prometheus-stack/app/prometheusrule-pod-pending.yaml`
and sibling `prometheusrule-*.yaml` files). If a rule referencing
`kube_*` metrics goes permanently inactive (not firing, not resolving
— just gone), check `kube-state-metrics` is `Running`+`Ready` before
suspecting the rule itself:

```sh
kubectl -n observability get pods -l app.kubernetes.io/name=kube-state-metrics
prom_execute_query 'up{job=~".*kube-state-metrics.*"}'
```

## Common failure triage — quick index

| Symptom | First check |
|---|---|
| Target not scraped | ServiceMonitor/PodMonitor selector vs. Service/pod labels — see "Why isn't my target being scraped?" above |
| No data in a Grafana panel, target IS scraped | Datasource UID matches the panel's `datasource` field; check the panel's PromQL/LogQL directly in Explore before suspecting the panel |
| Alert not firing when it should | `for:` clause elapsed? Check `ALERTS_FOR_STATE`; then check the `expr` actually matches current label values (`prom_execute_query` the raw expr) |
| Alert firing too much (flapping) | See "Is this alert flapping, or did I bury a real one?" above |
| A real alert seems to have been missed | Check `ALERTS{alertstate="firing"}` against Pushover history, then walk the routing tree + active `Silence` CRs — don't assume delivery failure |
| No logs for an app in Loki | Agent → aggregator → Loki pipeline, in that order (see Loki section) |
| No traces for an app in Tempo | App emitting OTLP at all? Then collector `memory_limiter`, then CNP egress (see Tempo section) |
| Grafana dashboard renders blank/broken after a ConfigMap edit | Confirm the dashboard JSON is valid and the `grafana_folder` annotation matches an existing provider path — a downloaded dashboard with no matching `dashboardProviders` entry crashes the init container, not just the panel |

## What this is NOT

- Not a substitute for `debugging.md`'s general pod/Flux/Ceph/Longhorn/etcd
  triage commands — this page only covers the observability stack
  itself.
- Not a substitute for `mcp_observability.md`, which owns the MCP
  fleet's own per-tool metrics and alerts.
- Not a design doc — see `.agents/instructions/schema.correction.md`
  for the full CRD-to-schema mapping used across this repo, and the
  observability persona's decision framework (flood vs. mute,
  routing correctness, successor/predecessor, maintenance vs.
  permanent silence) for how alerting *changes* should be evaluated
  before they're made.
