#!/usr/bin/env python3
"""Shared A2A client for the local-cron triage + doer jobs.

ONE tested contract for talking to a kagent operator over A2A JSON-RPC, so
triage crons and the doer pipeline no longer carry two divergent, hand-rolled
clients (a shell `curl … | jq` one-shot and a Python `urlopen` one-shot).

Contract:
  1. POST `message/send`.
  2. If the returned Task is already terminal with text, return it.
  3. Otherwise poll `tasks/get` until the Task reaches a terminal state or the
     time budget is exhausted.

`input-required` is treated as TERMINAL here on purpose: nobody can answer on an
unattended path, so we stop and return whatever we have rather than hang until
the deadline (see memory reference_kagent_ask_user_bricks_a2a).

`call_agent` NEVER raises for an operational failure (transport error, timeout,
empty body, non-terminal task). It returns `("", "<state>")` and lets the caller
decide whether that is a silent no-op (triage) or a logged skip (doer). This is
what turns a slow agent into a silent skipped run instead of a KubeJobFailed
page. Stdlib only — runs on the doer's python image and the triage python image
with nothing to pip-install.
"""
import json
import time
import urllib.request

# Terminal A2A task states. `working`/`submitted`/`auth-required` are the
# non-terminal ones we keep polling through.
TERMINAL = {"completed", "failed", "canceled", "rejected", "unknown", "input-required"}


def _parse_jsonrpc(raw):
    """Body may be a plain JSON-RPC object OR an SSE stream of `data: {…}` lines
    (the servers advertise `text/event-stream`). Return the last JSON object."""
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    obj = {}
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            chunk = line[5:].strip()
            if chunk and chunk != "[DONE]":
                try:
                    obj = json.loads(chunk)
                except Exception:
                    pass
    return obj


def _post(url, payload, timeout):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    })
    raw = urllib.request.urlopen(req, timeout=timeout).read().decode()
    return _parse_jsonrpc(raw)


def _extract_text(result):
    """Last text part across artifacts + status.message (kagent puts the final
    answer in one or the other depending on version)."""
    parts = []
    for a in (result.get("artifacts") or []):
        parts += a.get("parts") or []
    msg = (result.get("status", {}) or {}).get("message") or {}
    parts += msg.get("parts") or []
    texts = [p.get("text", "") for p in parts if p.get("kind") == "text"]
    return texts[-1] if texts else ""


def _state_of(result):
    return (result.get("status") or {}).get("state") or result.get("state") or "?"


def call_agent(agent_url, prompt, budget=600, poll_interval=10, message_id="local-cron"):
    """Send `prompt` to `agent_url`; return (final_text, state) within `budget` s.

    Returns ("", state) on any operational failure — never raises for one.
    """
    deadline = time.monotonic() + budget
    send = {
        "jsonrpc": "2.0", "id": 1, "method": "message/send",
        "params": {"message": {
            "role": "user",
            "parts": [{"kind": "text", "text": prompt}],
            "messageId": message_id, "kind": "message",
        }},
    }
    # Give the blocking send almost the whole budget (a synchronous server holds
    # the connection until the task finishes); keep a little back for polling if
    # the server instead returns early with a non-terminal task.
    try:
        d = _post(agent_url, send, timeout=max(30, budget - poll_interval))
    except Exception as e:
        return "", "transport-error: %r" % e

    result = d.get("result") or {}
    text = _extract_text(result)
    state = _state_of(result)
    task_id = result.get("id") or result.get("taskId")

    # Terminal (or a server that returns text with no state) → done.
    if text and (state in TERMINAL or state == "?"):
        return text, state
    if not task_id:
        return text, state

    # Non-terminal → poll tasks/get until terminal or budget exhausted.
    get = {"jsonrpc": "2.0", "id": 2, "method": "tasks/get", "params": {"id": task_id}}
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_interval, max(1, remaining)))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            d = _post(agent_url, get, timeout=max(15, min(60, remaining)))
        except Exception:
            continue
        result = d.get("result") or {}
        t = _extract_text(result)
        if t:
            text = t
        state = _state_of(result)
        if state in TERMINAL:
            break
    return text, state


# Substrings that mean the agent's MODEL BACKEND was unreachable — the GPU-backed
# serving (Ollama on the P40, vLLM on the Spark) behind LiteLLM, or LiteLLM itself
# mid-roll. Distinct from the model replying with garbage. Only consulted on a
# non-productive terminal state, so a healthy reply that happens to mention one of
# these words can't trip it (its state is `completed`, not `failed`).
_BACKEND_MARKERS = (
    "no deployments available", "connection refused", "stream_error",
    "apiconnectionerror", "service unavailable", "upstream connect error",
    "econnrefused", "litellm", ":4000", "503",
)


def is_backend_unavailable(text, state):
    """True when a run failed because the model backend (GPU) was unreachable — the
    retryable 'missing GPU, requeue and retry when available' case. Shared by the
    triage and doer crons so both requeue on the same signal."""
    # Transport failure reaching the agent pod itself (agent down / rolling).
    if state.startswith("transport-error"):
        return True
    # Agent reached but the task terminated 'failed': backend-down only if it
    # returned no text to classify, or its text names an upstream model/transport
    # error. A 'failed' task with a real (but untagged) reply is model drift, not a
    # GPU outage — that stays `malformed` for the caller to classify.
    if state == "failed":
        low = (text or "").lower()
        if not low.strip():
            return True
        if any(m in low for m in _BACKEND_MARKERS):
            return True
    return False
