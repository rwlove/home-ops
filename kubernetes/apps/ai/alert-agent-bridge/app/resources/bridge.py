#!/usr/bin/env python3
"""alert-agent-bridge — AlertManager webhook -> kagent observability-operator enrichment.

The event-driven counterpart to the scheduled triage crons. AlertManager POSTs a firing
alert group here; the bridge asks the in-cluster observability-operator agent to triage it
(read-only, local model, and it may delegate a domain deep-dive to a specialist via A2A),
then sends ONE supplemental email with the agent's finding via notify-mcp (/send). It does
NOT replace the alert's own page — AlertManager still routes the alert to its own receiver
in parallel (the route that forwards here uses continue: true). The email is quiet context
alongside the alert itself, which was the pager.

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
  OBSERVABILITY_AGENT_URL   default http://kagent-controller.ai:8083/mcp
  AGENT                     real kagent Agent CR name; default observability-operator
  AGENT_TIMEOUT_SECONDS     default 400
  MAX_INFLIGHT              default 2
  MAX_ALERTS_PER_GROUP      default 8
  PROM_URL                  Prometheus base URL for pre-gather; unset -> pre-gather off
  PREGATHER_TIMEOUT_SECONDS default 8 (per Prometheus query)
  NOTIFY_URL                default http://notify-mcp.mcp-system:8060/send (no auth)
  AGENT_NAME                default observability-operator (notify-mcp theme + sender)
"""
import json
import os
import re
import sys
import threading
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# kagent 1.0's ATE rewrite removed the per-agent Deployment + Service + raw-A2A
# endpoint this used to speak (http://<agent>.ai:8080/, message/send). Agents
# now run as transient sandboxed Actors; the only HTTP surface the controller
# exposes for invoking one is its own built-in MCP server (tools/call ->
# invoke_agent, kagent-dev/kagent#1160 — the same endpoint Claude Code uses via
# kagent-mcp.${SECRET_DOMAIN}/mcp). See local-cron/app/resources/a2a_client.py
# for the sibling client this mirrors (shared contract, not shared code — this
# bridge is intentionally dependency-free, no local-cron ConfigMap to import).
AGENT_URL = os.environ.get("OBSERVABILITY_AGENT_URL", "http://kagent-controller.ai:8083/mcp")
AGENT = os.environ.get("AGENT", "observability-operator")
AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT_SECONDS", "400"))
MAX_INFLIGHT = int(os.environ.get("MAX_INFLIGHT", "2"))
MAX_ALERTS = int(os.environ.get("MAX_ALERTS_PER_GROUP", "8"))
PROM_URL = os.environ.get("PROM_URL", "")
PREGATHER_TIMEOUT = int(os.environ.get("PREGATHER_TIMEOUT_SECONDS", "8"))
NOTIFY_URL = os.environ.get("NOTIFY_URL", "http://notify-mcp.mcp-system:8060/send")
AGENT_NAME = os.environ.get("AGENT_NAME", "observability-operator")

# Caps on how much we pre-gather per group, so an alert storm can't fan out queries.
MAX_PREGATHER_PODS = 5
MAX_PREGATHER_NODES = 4

_sem = threading.BoundedSemaphore(MAX_INFLIGHT)

# English-only send-gate. The local model intermittently drifts into a non-English
# language mid-reply despite the prompt contract (seen live 2026-11-16: a full-Chinese
# FluxHelmReleaseStalled enrichment reached Rob's inbox). Rob reads English only, so a
# CJK-bearing body must NEVER reach the email sink. This is the SAME deterministic gate
# the scheduled triage harness already enforces (local-cron/app/resources/triage_run.py,
# home-ops#15899) — the bridge is intentionally dependency-free (no shared import), so
# the mechanism is mirrored inline, not imported. Fixing this in the harness rather than
# by prompt-steering alone is the standing rule: the local model won't reliably obey the
# instruction, so the send path must enforce it. Ranges are integer codepoints so the
# source stays plain ASCII; threshold >=3 clears an odd stray fullwidth glyph while
# catching any real drift (which runs to hundreds of CJK chars).
_CJK_RANGES = (
    (0x3040, 0x30FF),  # Hiragana + Katakana
    (0x3400, 0x4DBF),  # CJK Unified Ext-A
    (0x4E00, 0x9FFF),  # CJK Unified
    (0xAC00, 0xD7AF),  # Hangul syllables
    (0xFF00, 0xFFEF),  # Halfwidth + Fullwidth forms
)
_CJK = re.compile(
    "[" + "".join("%s-%s" % (chr(lo), chr(hi)) for lo, hi in _CJK_RANGES) + "]")

# Corrective preface for the single re-ask when the first reply drifted non-English.
_CORRECTIVE_PREFACE = (
    "Your previous reply contained non-English (CJK) text. Reply in ENGLISH ONLY — "
    "every word, plain ASCII, no exceptions. Now redo the triage:\n\n"
)


def is_non_english(text):
    """True when the reply carries a meaningful amount of CJK text — the known
    local-model language-drift failure. Re-asked once, then suppressed."""
    return len(_CJK.findall(text)) >= 3


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
            "container": lbl.get("container", ""),
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
        container = a.get("container") or ""
        if not pod or (ns, pod) in seen_pods:
            continue
        seen_pods.add((ns, pod))
        if len(seen_pods) > MAX_PREGATHER_PODS:
            break
        sel = 'namespace="%s",pod="%s"' % (ns, pod)
        restarts = _first(_prom('max(kube_pod_container_status_restarts_total{%s})' % sel))
        info = _prom('kube_pod_info{%s}' % sel)
        node = (info[0][0].get("node") if info else "") or a.get("node") or ""
        # Memory limit + recent peaks: the numbers an OOM triage needs but can't infer.
        # cgroup usage_bytes is what the OOM killer actually enforces against memory.max;
        # working_set_bytes excludes reclaimable page cache, so a page-cache-driven OOM
        # (heavy file I/O, par2/unpack of large files) pins usage at the limit while
        # working_set reads far below the limit. Citing working_set alone is structurally
        # blind to that failure mode. Pull BOTH; scope to the alert's container when known,
        # else pod-wide.
        csel = sel + (',container="%s"' % container if container else ',container!=""')
        lim = _first(_prom('max(kube_pod_container_resource_limits{%s,resource="memory"})' % csel))
        peak = _first(_prom('max(max_over_time(container_memory_usage_bytes{%s}[30m]))' % csel))
        wss = _first(_prom('max(max_over_time(container_memory_working_set_bytes{%s}[30m]))' % csel))
        seg = "  %s/%s:" % (ns, pod)
        if container:
            seg += " container=%s" % container
        if restarts is not None:
            seg += " restarts=%s" % str(restarts).split(".")[0]
        if node:
            seg += " node=%s" % node.split(".")[0]
            if node not in nodes:
                nodes.append(node)
        try:
            if peak is not None and lim is not None:
                seg += " mem_usage_peak=%dMi/limit=%dMi" % (
                    round(float(peak) / 1048576), round(float(lim) / 1048576))
            elif peak is not None:
                seg += " mem_usage_peak=%dMi" % round(float(peak) / 1048576)
            if wss is not None:
                seg += " working_set_peak=%dMi" % round(float(wss) / 1048576)
        except (TypeError, ValueError):
            pass
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
        "be the cause, not the pod. For an OOMKilled/memory alert the relevant comparison "
        "is the pod's mem_usage_peak (cgroup-enforced) vs its limit above — NOT "
        "working_set_peak, which excludes reclaimable page cache. usage_peak at or near the "
        "limit while working_set_peak is far below means a page-cache-driven OOM under heavy "
        "file I/O (a headroom gap, not an RSS leak), not a contradiction. A healthy node "
        "does NOT rule out a per-container cgroup-limit OOM. If a pod is unhealthy, pull its "
        "failing container's logs via Loki to confirm before concluding.)"
    )


def _agent_prompt(alerts):
    lines = []
    for a in alerts[:MAX_ALERTS]:
        loc = (" ns=%s" % a["namespace"]) if a["namespace"] else ""
        pod = (" pod=%s" % a["pod"]) if a["pod"] else ""
        cont = (" container=%s" % a["container"]) if a.get("container") else ""
        sev = (" [%s]" % a["severity"]) if a["severity"] else ""
        lines.append("- %s%s%s%s%s: %s" % (a["alertname"], sev, loc, pod, cont, a["text"][:200]))
    listing = "\n".join(lines)
    evidence = _pregather(alerts)
    return (
        "An AlertManager group just fired. Triage it with your read-only tools. For each "
        "alert: real signal or transient noise, the likely cause, and the single next step "
        "you would propose. Live evidence is pre-gathered below where available — use it; "
        "it already answers 'is the node healthy' for the affected pods, so don't re-query "
        "that. If one alert is squarely another domain's expertise "
        "(storage / network / ml / smart-home), you MAY delegate ONE focused deep-dive to "
        "that specialist and fold their finding in — but tell the specialist this is an "
        "unattended one-way pipeline: it must answer directly from read-only inspection and "
        "MUST NOT ask for confirmation or request any input. A specialist that stalls "
        "waiting for approval hangs this whole enrichment until it times out, so if it "
        "cannot answer without confirming an action, it should decline the deep-dive and "
        "report what it found read-only. For any OOMKilled or memory alert, your reply MUST "
        "name the workload (namespace/pod/container) and state its memory limit and recent "
        "mem_usage_peak (cgroup-enforced) from the pre-gathered evidence — never omit the "
        "identity or those numbers. An OOMKill is a per-container cgroup-limit event: compare "
        "mem_usage_peak vs limit, NOT working_set and NOT node memory. If mem_usage_peak sits "
        "at the limit while working_set_peak is far below, that is a page-cache-driven OOM "
        "under heavy file I/O (a headroom gap, not a leak) — do not treat it as a "
        "contradiction to explain away. This is a one-way enrichment note that "
        "rides alongside the page AlertManager already sent — nobody can reply, so never ask "
        "a question. If it's all benign/known noise, say so briefly. Reply in ENGLISH "
        "ONLY — every word, plain ASCII. Keep the whole reply "
        "under 1400 characters, plain text, no markdown.\n\nFiring alerts:\n" + listing + evidence
    )


def _parse_jsonrpc(raw):
    """Body may be a plain JSON-RPC object OR an SSE stream of `data: {…}` lines
    (kagent's MCP endpoint advertises text/event-stream). Return the last JSON
    object. Mirrors local-cron/app/resources/a2a_client.py's parser."""
    raw = raw.strip()
    if raw.startswith("{"):
        return json.loads(raw)
    obj = {}
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            chunk = line[5:].strip()
            if chunk and chunk != "[DONE]":
                try:
                    obj = json.loads(chunk)
                except Exception:  # noqa: BLE001
                    pass
    return obj


def _task_dump_text(s):
    """kagent falls back to serializing the WHOLE A2A task into the content part when
    the agent ends without a final text answer (e.g. the strong tier failed over to the
    7B model mid-run and it stopped on a tool call). Emailing that verbatim sent Rob a
    wall of function_call/function_response JSON (2026-11-21 SparkVllmDriverPodDown).
    If `s` is such a dump, return only the agent's prose text parts (status message
    first, else the last artifact carrying text); '' when there is none. Anything
    that is not an A2A task dump is returned unchanged."""
    st = (s or "").strip()
    if not st.startswith("{"):
        return s or ""
    try:
        task = json.loads(st)
    except ValueError:
        return s
    # Only an A2A Task shape counts as a dump; any other JSON object is a legitimate
    # reply (the doer accepts a bare {...} intent proposal) and passes through.
    if not isinstance(task, dict) or not ({"artifacts", "history"} & set(task)
                                          or task.get("kind") == "task"):
        return s

    def texts(parts):
        out = []
        for p in parts or []:
            if not isinstance(p, dict):
                continue
            ptype = ((p.get("metadata") or {}).get("kagent.dev/a2a/part-type") or "")
            if ptype.startswith("function_") or "data" in p:
                continue
            t = p.get("text")
            if isinstance(t, str) and t.strip():
                out.append(t.strip())
        return out

    msg = (task.get("status") or {}).get("message") or {}
    found = texts(msg.get("parts"))
    if not found:
        for art in reversed(task.get("artifacts") or []):
            found = texts((art or {}).get("parts"))
            if found:
                break
    return "\n\n".join(found)


def _call_agent(prompt):
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "invoke_agent",
            "arguments": {
                "agent": {"namespace": "ai", "name": AGENT},
                "message": prompt,
                # message_id is a STICKY SESSION KEY in the kagent controller, not a
                # request id (home-ops#15934 / project_kagent_broker_handshake_stale_catalog
                # in memory) — reusing a constant here made every invocation replay the
                # first-ever cached task result ("PONG", from a connectivity smoke test)
                # instead of running fresh triage. Unique per call, mirroring the fix
                # already applied in local-cron/app/resources/a2a_client.py.
                "message_id": "alert-bridge-%s" % uuid.uuid4(),
            },
        },
    }).encode()
    req = urllib.request.Request(AGENT_URL, data=body, headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    })
    with urllib.request.urlopen(req, timeout=AGENT_TIMEOUT) as r:
        resp = _parse_jsonrpc(r.read().decode())
    if resp.get("error"):
        raise RuntimeError("invoke_agent error: %r" % resp["error"])
    result = resp.get("result", {}) or {}
    sc = result.get("structuredContent") or {}
    if sc.get("text"):
        return _task_dump_text(sc["text"])
    parts = [p.get("text", "") for p in (result.get("content") or [])
             if p.get("type") == "text"]
    return _task_dump_text(parts[-1]) if parts else ""


def _notify(subject, body):
    """POST a themed HTML email through notify-mcp's /send route. No auth: notify-mcp
    holds the relay recipient-lock, so it can only ever email Rob. Non-fatal — a failed
    send is logged, never raised (the alert still paged via AlertManager separately)."""
    try:
        data = json.dumps({"subject": subject, "body": body, "agent": AGENT_NAME}).encode()
        req = urllib.request.Request(
            NOTIFY_URL, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            if r.status // 100 != 2:
                raise RuntimeError("notify-mcp HTTP %s" % r.status)
        log("enrichment emailed")
    except Exception as e:  # noqa: BLE001
        log("notify-mcp send failed: %r" % e)


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
            # Enrichment failed (timeout, stuck A2A delegation, agent down). Do NOT
            # email a degraded fallback: AlertManager has already paged this alert
            # independently (the route that forwards here uses continue: true), so a
            # raw-detail email carrying no enrichment is a pure duplicate of the page
            # Rob already got — the non-actionable noise the watcher rules forbid.
            # Log only; the page stands on its own.
            #
            # LOAD-BEARING ASSUMPTION: every alert routed to this bridge also pages via
            # AlertManager (continue: true on the forwarding route). If a future route
            # ever forwards here WITHOUT an independent page, suppressing here would
            # silently drop it — keep continue: true, or re-introduce a fallback.
            log("agent call failed for [%s]: %r — fallback email suppressed "
                "(AlertManager paged independently)" % (names, e))
            return
        if not text:
            log("agent returned no text for [%s]" % names)
            return
        # English-only send-gate. If the reply drifted into CJK, re-ask ONCE with a
        # corrective preface (a fresh message_id -> fresh session), then suppress if it
        # still drifts. Suppressing loses only supplemental context, never the page —
        # AlertManager paged this alert independently (continue: true on the forwarding
        # route), the same load-bearing assumption the agent-failure branch above relies on.
        if is_non_english(text):
            log("non-English reply (CJK detected) for [%s] — re-asking once "
                "with corrective preface" % names)
            try:
                text = _call_agent(_CORRECTIVE_PREFACE + _agent_prompt(alerts))
            except Exception as e:  # noqa: BLE001
                log("re-ask failed for [%s]: %r — enrichment suppressed" % (names, e))
                return
            if not text or is_non_english(text):
                log("reply still non-English after re-ask for [%s] — enrichment "
                    "suppressed (English-only; AlertManager paged independently)" % names)
                return
        _notify("kagent enrichment: %s" % names, text)
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
