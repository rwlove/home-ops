#!/usr/bin/env python3
"""kagent fleet end-to-end dead-man's-switch exporter.

Loops forever: every PROBE_INTERVAL seconds it runs the proven A2A probe
against ONE canary kagent operator agent (ml-operator by default -- Green
tier, read-only built-in tool, no destructive path). A PASS requires the
task to reach TASK_STATE_COMPLETED *and* carry at least one `function_call`
artifact part, which proves the MCP-tool leg actually fired -- not just an
LLM text reply. On a PASS it stamps kagent_e2e_last_success_timestamp_seconds
with the current unix time.

Serves Prometheus metrics on :METRICS_PORT /metrics continuously. The
companion PrometheusRule (KagentFleetE2EStale) pages when that timestamp
goes stale (> 2h, i.e. >= 4 missed 30m cycles) OR the series goes absent
(this exporter down/unscraped) -- a dead-man's-switch that still pages when
its own producer dies.

Stdlib only (http.server + urllib) -- no pip deps, runs on python:*-slim.

Zero Anthropic/Claude tokens: this hits only the in-cluster kagent
controller, which runs local-GPU inference (litellm -> Ollama/vLLM).

Data classification: this NEVER logs or exposes the task's reply text or
artifact payloads (they could carry internal cluster detail) -- output is
limited to the task-state string and booleans.
"""

import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONTROLLER_URL = os.environ.get("CONTROLLER_URL", "http://kagent-controller.ai:8083")
AGENT_NAMESPACE = os.environ.get("CANARY_AGENT_NAMESPACE", "ai")
AGENT = os.environ.get("CANARY_AGENT", "ml-operator")
PROBE_INTERVAL = int(os.environ.get("PROBE_INTERVAL_SECONDS", "1800"))
METRICS_PORT = int(os.environ.get("METRICS_PORT", "9115"))
REQUEST_TIMEOUT = int(os.environ.get("PROBE_TIMEOUT_SECONDS", "120"))
PROMPT = os.environ.get(
    "PROBE_PROMPT",
    "Use your k8s_get_available_api_resources tool once, then reply with "
    "exactly the single word DONE and nothing else.",
)

LOCK = threading.Lock()
STATE = {"last_success": 0.0, "up": 0, "duration": 0.0}


def _invoke(msg_id):
    """POST one A2A SendMessage to the controller's raw per-agent endpoint.

    Shape is the proven one from .agents/skills/kagent-e2e-verify.md: the
    raw /agents/<ns>/<name> endpoint is the only surface that exposes the
    task's `artifacts`, which carry the structural proof a tool was called.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "SendMessage",
        "params": {
            "message": {
                "role": "ROLE_USER",
                "parts": [{"kind": "text", "text": PROMPT}],
                "messageId": msg_id,
                "kind": "message",
            }
        },
    }
    url = "%s/agents/%s/%s" % (CONTROLLER_URL, AGENT_NAMESPACE, AGENT)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "A2A-Version": "1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def _evaluate(resp):
    """Return (state_string, tool_called_bool). Reads only structure."""
    task = ((resp or {}).get("result") or {}).get("task") or {}
    state = ((task.get("status") or {}).get("state")) or ""
    tool_called = any(
        ((part.get("metadata") or {}).get("kagent.dev/a2a/part-type") == "function_call")
        for art in (task.get("artifacts") or [])
        for part in (art.get("parts") or [])
    )
    return state, tool_called


def run_cycle():
    """One probe cycle: invoke, retry once, return (ok, state_label, duration).

    Retries once within the cycle before marking it a FAIL -- a single
    "the model skipped the tool this time" is a model quirk; two in a row
    is real signal (same rationale as the kagent-e2e-verify skill).
    """
    start = time.monotonic()
    ok = False
    label = "no-response"
    for attempt in range(2):
        msg_id = "kagent-e2e-watchdog-%d-%d" % (int(time.time()), attempt)
        try:
            state, tool_called = _evaluate(_invoke(msg_id))
            label = state or "empty-response"
            if state == "TASK_STATE_COMPLETED" and tool_called:
                ok = True
                break
            if state == "TASK_STATE_COMPLETED" and not tool_called:
                label = "completed-without-tool"
        except Exception as exc:  # noqa: BLE001 -- label by type only, never payload
            label = "error:%s" % type(exc).__name__
    return ok, label, time.monotonic() - start


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 -- stdlib handler contract
        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            return
        with LOCK:
            last = STATE["last_success"]
            up = STATE["up"]
            dur = STATE["duration"]
        body = (
            "# HELP kagent_e2e_last_success_timestamp_seconds Unix time of the last PASS cycle (TASK_STATE_COMPLETED with a real function_call artifact) against the canary kagent agent.\n"
            "# TYPE kagent_e2e_last_success_timestamp_seconds gauge\n"
            "kagent_e2e_last_success_timestamp_seconds %r\n"
            "# HELP kagent_e2e_up Whether the most recent probe cycle passed (1) or failed (0).\n"
            "# TYPE kagent_e2e_up gauge\n"
            "kagent_e2e_up %d\n"
            "# HELP kagent_e2e_probe_duration_seconds Wall-clock duration of the most recent probe cycle, in seconds.\n"
            "# TYPE kagent_e2e_probe_duration_seconds gauge\n"
            "kagent_e2e_probe_duration_seconds %r\n"
        ) % (last, up, dur)
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # silence per-request access logging
        return


def main():
    # Startup value: seed last_success with the process start time, NOT 0.
    # The alert fires on (time() - max(last_success)) > 7200, so seeding 0
    # would make a freshly-deployed/restarted exporter look ~55 years stale
    # and flap on every rollout. Seeding boot-time gives the exporter a
    # grace window exactly equal to the staleness threshold (2h) to land its
    # first PASS -- and if the fleet is genuinely broken at boot, the alert
    # still fires correctly once that window elapses.
    with LOCK:
        STATE["last_success"] = time.time()
        STATE["up"] = 1
        STATE["duration"] = 0.0

    server = ThreadingHTTPServer(("", METRICS_PORT), MetricsHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(
        "kagent-e2e-watchdog: /metrics on :%d; probing %s/%s every %ds (timeout %ds)"
        % (METRICS_PORT, AGENT_NAMESPACE, AGENT, PROBE_INTERVAL, REQUEST_TIMEOUT),
        flush=True,
    )

    while True:
        ok, label, duration = run_cycle()
        now = time.time()
        with LOCK:
            STATE["up"] = 1 if ok else 0
            STATE["duration"] = duration
            if ok:
                STATE["last_success"] = now
        print(
            "cycle result=%s state=%s duration=%.1fs"
            % ("PASS" if ok else "FAIL", label, duration),
            flush=True,
        )
        time.sleep(PROBE_INTERVAL)


if __name__ == "__main__":
    main()
