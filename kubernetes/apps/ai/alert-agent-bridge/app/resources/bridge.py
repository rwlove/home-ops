#!/usr/bin/env python3
"""alert-agent-bridge — AlertManager webhook -> kagent observability-operator enrichment.

The event-driven counterpart to the scheduled triage crons. AlertManager POSTs a firing
alert group here; the bridge asks the in-cluster observability-operator agent to triage it
(read-only, local model, and it may delegate a domain deep-dive to a specialist via A2A),
then sends ONE supplemental Pushover with the agent's finding. It does NOT replace the
alert's own page — AlertManager still routes the alert to the pushover receiver in
parallel (the route that forwards here uses continue: true). This message is priority 0
(quiet context), the alert itself was the pager.

Design points:
  * ACK the webhook immediately (202) and do the slow agent call on a background thread —
    AlertManager's webhook client times out in seconds and would retry (duplicate work)
    if we blocked on the ~1-2 min agent inference.
  * A bounded worker semaphore caps concurrent agent calls so an alert storm can't spawn
    unbounded threads or hammer the local model; excess groups are dropped with a log
    (the alert still paged via AlertManager).
  * Deterministic pre-gather (Prometheus): for pod-scoped alerts we fetch the hard
    signal the local model most reliably fails to construct on its own — restart count,
    the pod's NODE, and that node's temperature / load / CPU — and hand it to the agent
    as evidence. This front-loads the node-health chain that distinguishes "the pod is
    broken" from "the node is broken" (the failure mode a symptom-only triage misses).
    Logs stay with the agent (it has query_loki_logs and fetching-by-pod is trivial).
  * Enrichment is best-effort: any failure (incl. every Prometheus query) is logged and
    degrades gracefully — the agent still gets the alert, never a crash.

Stdlib only. Config via env:
  OBSERVABILITY_AGENT_URL   default http://observability-operator.ai:8080/
  AGENT_TIMEOUT_SECONDS     default 400
  MAX_INFLIGHT              default 2
  MAX_ALERTS_PER_GROUP      default 8
  PROM_URL                  Prometheus base URL for pre-gather; unset -> pre-gather off
  PREGATHER_TIMEOUT_SECONDS default 8 (per Prometheus query)
  PUSHOVER_TOKEN, PUSHOVER_USER   (required to send; missing -> log-only)
"""
import json
import os
import sys
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AGENT_URL = os.environ.get("OBSERVABILITY_AGENT_URL", "http://observability-operator.ai:8080/")
AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT_SECONDS", "400"))
MAX_INFLIGHT = int(os.environ.get("MAX_INFLIGHT", "2"))
MAX_ALERTS = int(os.environ.get("MAX_ALERTS_PER_GROUP", "8"))
PROM_URL = os.environ.get("PROM_URL", "")
PREGATHER_TIMEOUT = int(os.environ.get("PREGATHER_TIMEOUT_SECONDS", "8"))
PUSHOVER_TOKEN = os.environ.get("PUSHOVER_TOKEN", "")
PUSHOVER_USER = os.environ.get("PUSHOVER_USER", "")
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"

# Caps on how much we pre-gather per group, so an alert storm can't fan out queries.
MAX_PREGATHER_PODS = 5
MAX_PREGATHER_NODES = 4

_sem = threading.BoundedSemaphore(MAX_INFLIGHT)


def log(msg):
    print("alert-agent-bridge: %s" % msg, flush=True)


def _firing(payload):
    """Extract firing alerts as compact dicts (name, ns, severity, text, pod, node)."""
    out = []
    for a in payload.get("alerts", []):
        if a.get("status") != "firing":
            continue
        lbl = a.get("labels", {}) or {}
        ann = a.get("annotations", {}) or {}
        text = ann.get("description") or ann.get("summary") or ann.get("message") or ""
        out.append({
            "alertname": lbl.get("alertname", "?"),
            "namespace": lbl.get("namespace", ""),
            "severity": lbl.get("severity", ""),
            "text": text,
            "pod": lbl.get("pod", ""),
            "node": lbl.get("node", ""),
        })
    return out


def _prom(query):
    """Prometheus instant query -> list of (labels, value_str). [] on any failure."""
    if not PROM_URL:
        return []
    try:
        url = PROM_URL.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode({"query": query})
        with urllib.request.urlopen(url, timeout=PREGATHER_TIMEOUT) as r:
            d = json.loads(r.read().decode())
        if d.get("status") != "success":
            return []
        return [(x.get("metric", {}) or {}, (x.get("value") or [None, ""])[1])
                for x in (d.get("data", {}) or {}).get("result", [])]
    except Exception as e:  # noqa: BLE001
        log("prom query failed (%s): %r" % (query[:60], e))
        return []


def _first(rows):
    return rows[0][1] if rows else None


def _pregather(alerts):
    """Deterministic Prometheus evidence for pod-scoped alerts: restarts, the pod's node,
    and that node's temp/load/cpu. Returns a compact text block, or '' (best-effort)."""
    if not PROM_URL:
        return ""
    lines = []
    nodes = []          # ordered, deduped full nodenames
    seen_pods = set()
    for a in alerts[:MAX_ALERTS]:
        ns, pod = a.get("namespace"), a.get("pod")
        if not pod or (ns, pod) in seen_pods:
            continue
        seen_pods.add((ns, pod))
        if len(seen_pods) > MAX_PREGATHER_PODS:
            break
        sel = 'namespace="%s",pod="%s"' % (ns, pod)
        restarts = _first(_prom('max(kube_pod_container_status_restarts_total{%s})' % sel))
        info = _prom('kube_pod_info{%s}' % sel)
        node = (info[0][0].get("node") if info else "") or a.get("node") or ""
        seg = "  %s/%s:" % (ns, pod)
        if restarts is not None:
            seg += " restarts=%s" % str(restarts).split(".")[0]
        if node:
            seg += " node=%s" % node.split(".")[0]
            if node not in nodes:
                nodes.append(node)
        lines.append(seg)

    for node in nodes[:MAX_PREGATHER_NODES]:
        inst = '%s.*' % node  # node-exporter instance is "<node>:9100"
        temp = _first(_prom('max(node_hwmon_temp_celsius{instance=~"%s"})' % inst))
        load = _first(_prom('max(node_load1{instance=~"%s"})' % inst))
        cpu = _first(_prom('1 - avg(rate(node_cpu_seconds_total{instance=~"%s",mode="idle"}[5m]))' % inst))
        parts = []
        try:
            if temp is not None:
                parts.append("temp=%d°C" % round(float(temp)))
            if load is not None:
                parts.append("load1=%.1f" % float(load))
            if cpu is not None:
                parts.append("cpu=%d%%" % round(float(cpu) * 100))
        except (TypeError, ValueError):
            pass
        if parts:
            lines.append("  node %s: %s" % (node.split(".")[0], ", ".join(parts)))

    if not lines:
        return ""
    return (
        "\n\nPre-gathered evidence (Prometheus, live):\n" + "\n".join(lines) +
        "\n(A node at temp>=90°C or load1 far above its core count means the NODE may "
        "be the cause, not the pod. If a pod is unhealthy, pull its failing container's "
        "logs via Loki to confirm before concluding.)"
    )


def _agent_prompt(alerts):
    lines = []
    for a in alerts[:MAX_ALERTS]:
        loc = (" ns=%s" % a["namespace"]) if a["namespace"] else ""
        pod = (" pod=%s" % a["pod"]) if a["pod"] else ""
        sev = (" [%s]" % a["severity"]) if a["severity"] else ""
        lines.append("- %s%s%s%s: %s" % (a["alertname"], sev, loc, pod, a["text"][:200]))
    listing = "\n".join(lines)
    evidence = _pregather(alerts)
    return (
        "An AlertManager group just fired. Triage it with your read-only tools. For each "
        "alert: real signal or transient noise, the likely cause, and the single next step "
        "you would propose. Live evidence is pre-gathered below where available — use it; "
        "it already answers 'is the node healthy' for the affected pods, so don't re-query "
        "that. If one alert is squarely another domain's expertise "
        "(storage / network / ml / smart-home), you MAY delegate ONE focused deep-dive to "
        "that specialist and fold their finding in. This is a one-way enrichment note that "
        "rides alongside the page AlertManager already sent — nobody can reply, so never ask "
        "a question. If it's all benign/known noise, say so briefly. Keep the whole reply "
        "under 900 characters, plain text, no markdown.\n\nFiring alerts:\n" + listing + evidence
    )


def _call_agent(prompt):
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "message/send",
        "params": {"message": {
            "role": "user",
            "parts": [{"kind": "text", "text": prompt}],
            "messageId": "alert-bridge", "kind": "message",
        }},
    }).encode()
    req = urllib.request.Request(AGENT_URL, data=body, headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    })
    with urllib.request.urlopen(req, timeout=AGENT_TIMEOUT) as r:
        resp = json.loads(r.read().decode())
    result = resp.get("result", {}) or {}
    parts = []
    for art in result.get("artifacts", []) or []:
        parts += art.get("parts", []) or []
    msg = (result.get("status", {}) or {}).get("message", {}) or {}
    parts += msg.get("parts", []) or []
    texts = [p.get("text", "") for p in parts if p.get("kind") == "text" and p.get("text")]
    return texts[-1] if texts else ""


def _pushover(title, message, priority=0):
    if not (PUSHOVER_TOKEN and PUSHOVER_USER):
        log("Pushover creds unset — would send: %s / %s" % (title, message[:80]))
        return
    data = urllib.parse.urlencode({
        "token": PUSHOVER_TOKEN, "user": PUSHOVER_USER,
        "title": title[:250], "message": message[:1000], "priority": priority,
    }).encode()
    req = urllib.request.Request(PUSHOVER_URL, data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.status // 100 != 2:
            raise RuntimeError("Pushover HTTP %s" % r.status)


def _handle(payload):
    try:
        alerts = _firing(payload)
        if not alerts:
            log("no firing alerts in group — nothing to enrich")
            return
        names = ", ".join(sorted({a["alertname"] for a in alerts}))[:120]
        log("enriching group: %s (%d firing)" % (names, len(alerts)))
        try:
            text = _call_agent(_agent_prompt(alerts))
        except Exception as e:  # noqa: BLE001
            log("agent call failed for [%s]: %r" % (names, e))
            _pushover("kagent enrichment failed: %s" % names,
                      "observability-operator did not respond: %s" % str(e)[:200],
                      priority=-1)
            return
        if not text:
            log("agent returned no text for [%s]" % names)
            return
        _pushover("kagent enrichment: %s" % names, text, priority=0)
        log("enrichment sent for [%s]" % names)
    finally:
        _sem.release()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path != "/alerts":
            self.send_response(404)
            self.end_headers()
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(n).decode()) if n else {}
        except Exception as e:  # noqa: BLE001
            log("bad webhook body: %r" % e)
            self.send_response(400)
            self.end_headers()
            return
        # ACK immediately; process off-thread so AlertManager never times out/retries.
        if _sem.acquire(blocking=False):
            threading.Thread(target=_handle, args=(payload,), daemon=True).start()
            self.send_response(202)
        else:
            log("max inflight (%d) reached — dropping enrichment (alert still paged)" % MAX_INFLIGHT)
            self.send_response(429)
        self.end_headers()

    def log_message(self, *args):
        pass  # quiet; we log our own lines


if __name__ == "__main__":
    log("starting on :8080 -> %s (timeout %ds, max_inflight %d, pregather=%s)" % (
        AGENT_URL, AGENT_TIMEOUT, MAX_INFLIGHT, "on" if PROM_URL else "off"))
    try:
        ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
