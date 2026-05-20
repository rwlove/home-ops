# MCP Fleet Observability

The MCP fleet under `mcp-system` ships per-tool Prometheus metrics for the
Class A backends we own (`memory-mcp`, `time-mcp`, `netbox-mcp`) plus
Istio-sidecar RED metrics for everything else. Alerts and a Grafana
dashboard sit on top.

| | |
|---|---|
| **Dashboard** | `mcp-system` (Grafana folder `mcp`) |
| **Recording rules + alerts** | `kubernetes/apps/mcp-system/observability/app/prometheusrule.yaml` |
| **Per-backend ServiceMonitors** | `kubernetes/apps/mcp-system/<backend>/app/servicemonitor.yaml` |
| **Dashboard JSON** | `kubernetes/apps/observability/grafana/dashboards/mcp-system.json` |
| **Plan archive** | `~/.claude-personal/plans/on-a-plan-to-adaptive-ripple.md` (v5, 4-pass adversarial audit) |

## What's instrumented today

| Class | Backends | Layer |
|---|---|---|
| A — per-tool metrics | `memory-mcp`, `time-mcp`, `netbox-mcp` | App-level `prometheus-client` + `@track_tool` decorator + ServiceMonitor |
| D — istio only | `chrome-mcp`, `arr-mcp`, `github-mcp`, `grafana-mcp`, `ha-mcp`, `immich-mcp`, `kubectl-mcp`, `omada-mcp`, `paperless-mcp`, `prometheus-mcp`, `searxng-mcp` | Envoy sidecar emits `istio_requests_total` per pod; no app-level work |

Class B/C never materialized: a 2026-05-19 sweep found that none of the
upstream third-party backends expose `/metrics` today
(see `project_mcp_phase3_metrics_sweep.md`).

## How to read the dashboard

Two rows:

**Row 1 — Fleet (istio sidecar)** covers all 14 backends. Inbound +
outbound rate panels + 5xx panels by `destination_workload` and
`source_workload`. Especially useful for `chrome-mcp` / `searxng-mcp`
where the meaningful metric is whether the outbound HTTPS calls
succeeded.

**Row 2 — Per-backend (`@track_tool`)** is the deep view, gated by
the `$backend` template variable. Panels query the normalized
`mcp:tool_calls:rate5m{backend, ...}` / `mcp:tool_call_duration:p99_5m`
recording rules, so adding a 4th Class A backend doesn't require panel
edits — the variable enumerates from the recording rules.

## Alerts

Six alerts, six labels:

| Alert | Tier | Routing | Fires when |
|---|---|---|---|
| `MCPBackendScrapeFailed{backend=memory}` | warning | Pushover | `absent(up{...memory-mcp} == 1) for: 15m` |
| `MCPBackendScrapeFailed{backend=time}` | warning | Pushover | same, time-mcp |
| `MCPBackendScrapeFailed{backend=netbox}` | warning | Pushover | same, netbox-mcp |
| `MCPBackendHighErrorRate` | warning | Pushover | error/total > 10% for 10m AND volume > 0.05 calls/sec |
| `MCPBackendHighLatency` | warning | Pushover | `max by (backend) (mcp:tool_call_duration:p99_5m) > 5s for: 10m` |
| `MCPGatewayDown` | **critical** | `windmill-investigate` (HolmesGPT) → Pushover | `absent(up{...mcp-gateway-istio} == 1) for: 5m` |

The error-rate volume gate (`> 0.05 calls/sec`) prevents a single
errored tool call on a quiet backend from triggering a 100%-error
page. The latency `for: 10m` rides through transient downstream
spikes (Ollama warm-up, Postgres vacuum).

## Adding a new MCP backend with metrics

Three places to touch:

**1. Upstream code (the backend's source repo).** Mirror the
memory-mcp v0.1.4 template:

```python
# src/<backend>/metrics.py
from prometheus_client import Counter, Histogram

TOOL_CALLS_TOTAL = Counter(
    "<backend>_mcp_tool_calls_total",
    "Total tool calls into <backend>-mcp.",
    labelnames=("tool", "status"),
)
TOOL_CALL_DURATION_SECONDS = Histogram(
    "<backend>_mcp_tool_call_duration_seconds",
    "Tool call latency in seconds.",
    labelnames=("tool",),
    buckets=(...),  # tune to expected latency
)

def track_tool(name): ...  # see memory_mcp/metrics.py
```

For backends with **many tools**, do not edit every `@mcp.tool()`
manually. Add an `install_tracking(mcp)` helper that monkey-patches
`mcp.tool` to wrap every subsequent registration (`netbox-mcp` does
this — `rwlove/containers#20`).

Register `/metrics` on the same FastMCP HTTP server via
`mcp.custom_route("/metrics", methods=["GET"])`.

Add `prometheus-client>=0.20.0,<1` to the project's dependencies.

**Local test before pushing:**

```
python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:<port>/metrics').read().decode()[:1000])"
```

Verify `# HELP <backend>_mcp_tool_calls_total ...` and `# TYPE`
directives appear. Invoke each tool at least once; counters increment.

**2. Cluster manifests.** Add to
`kubernetes/apps/mcp-system/<backend>/app/`:

```yaml
# servicemonitor.yaml — clone time-mcp's verbatim, change name + selector
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: <backend>-mcp
  labels:
    app.kubernetes.io/name: <backend>-mcp
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: <backend>-mcp
  endpoints:
    - port: http
      path: /metrics
      interval: 30s
      scrapeTimeout: 10s
  namespaceSelector:
    matchNames:
      - mcp-system
```

Add `./servicemonitor.yaml` to the app's `kustomization.yaml`
resources list.

**3. Recording rule group.** Append to
`kubernetes/apps/mcp-system/observability/app/prometheusrule.yaml`:

```yaml
    - name: mcp.recording.<backend>.rules
      interval: 1m
      rules:
        - record: mcp:tool_calls:rate5m
          expr: sum by (tool, status) (rate(<backend>_mcp_tool_calls_total[5m]))
          labels:
            backend: <backend>
        - record: mcp:tool_call_duration:p99_5m
          expr: |
            histogram_quantile(
              0.99,
              sum by (le, tool) (rate(<backend>_mcp_tool_call_duration_seconds_bucket[5m]))
            )
          labels:
            backend: <backend>
```

After this lands, `MCPBackendHighErrorRate` and `MCPBackendHighLatency`
automatically fan out to cover the new backend. **You must also add a
new `MCPBackendScrapeFailed` alert** because each `absent()` clause
needs its own selector (the per-backend split is the documented
exception in the plan).

**4. Optional: dashboard.** The `$backend` template variable
auto-enumerates from `label_values(mcp:tool_calls:rate5m, backend)`, so
the dashboard surfaces the new backend without JSON edits.

## Silencing alerts during planned maintenance

`silence-operator` (in the `observability` ns) translates declarative
`Silence` CRs into AlertManager silences. For a planned maintenance
window:

```yaml
apiVersion: observability.giantswarm.io/v1alpha2
kind: Silence
metadata:
  name: mcp-maintenance-<short-description>
  namespace: observability
spec:
  matchers:
    - name: alertname
      value: MCPBackendScrapeFailed
      isRegex: false
    - name: backend
      value: netbox        # or omit for all backends
      isRegex: false
  comment: "Planned maintenance: <link / context>"
  duration: 2h
```

Apply, do the work, delete the Silence (or let it expire).

For the broader fleet you can match `namespace=mcp-system` instead of
`alertname` — that catches `MCPGatewayDown` too.

## Cardinality budget

Per Class A backend (assuming ~10 tools, 11 latency buckets):

- `_tool_calls_total{tool, status}`: 10 × 2 = **20 series**
- `_tool_call_duration_seconds_*{tool, le}`: 10 × (12 buckets + sum + count) = **140 series**
- Embedder (memory-mcp only, `_embed_*`): **17 series**
- Total: **~177 series per backend**

3 backends × 177 ≈ **530 series**. Plus istio sidecar's
`istio_requests_total` + latency histograms — already scraped by
the Istio control-plane SM, not new growth.

Cluster Prometheus runs at several million series — adequate headroom
for the next ~50 backends. **Do not** add high-cardinality labels
(`user`, `session`, `request_id`, error messages, tool arguments).
Those belong in log lines, not metric labels.

## Why not Class C / fork upstream?

The 2026-05-19 sweep checked `/metrics` on every third-party backend.
All 9 returned 404. Three upstream repos (`jmtvms/tplink-omada-mcp`,
`isokoliuk/mcp-searxng`, and an aplaceforallmystuff redirect) are gone
from GitHub — only the published images remain on registries.

For the remaining live upstream repos (`grafana/mcp-grafana`,
`pab1it0/prometheus-mcp-server`, `rohitg00/kubectl-mcp-server`,
`github/github-mcp-server`, `homeassistant-ai/ha-mcp`,
`baruchiro/paperless-mcp`, `joeru/claw2immich`,
`microsoft/playwright-mcp`), filing upstream issues is the
documented next step **only if a specific operational pain triggers
it** — not a checklist. The plan capped per-backend effort at 1
upstream issue + 1 PR + 2-week wait, demote to Class D otherwise.

For now: Istio sidecar's `istio_requests_total{destination_workload,
response_code}` answers "is this backend serving traffic?" and "is it
returning errors?" — the RED metrics. Per-tool granularity is the
upgrade we'd buy by forking; not worth the ownership cost yet.

## References

- Source code (per-tool decorator + `/metrics` template):
  - `/home/rwlove/workspace/memory-mcp/src/memory_mcp/metrics.py` (canonical async version)
  - `/home/rwlove/workspace/containers/time-mcp/server.py` (sync variant)
  - `/home/rwlove/workspace/containers/netbox-mcp/src/netbox_mcp_server/metrics.py` (auto-install variant)
- AlertManager routing: `kubernetes/apps/observability/kube-prometheus-stack/app/alertmanagerconfig.yaml`
- Cluster scrape-failure convention (origin of the `absent(up{} == 1)` pattern):
  `kubernetes/apps/observability/exporters/snmp-exporter/app/apc-ups/prometheusrule.yaml`
- Rollout PRs: home-ops #11741 (substrate), #11749 (Phase 1+2 cluster bumps), #11789 (Phase 5 alerts); rwlove/containers #19 (time-mcp v0.1.1), #20 (netbox-mcp v1.1.0)
