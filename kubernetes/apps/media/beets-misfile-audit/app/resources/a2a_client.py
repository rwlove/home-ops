#!/usr/bin/env python3
"""Shared agent-invocation client for the local-cron triage + doer jobs.

ONE tested contract for talking to a kagent operator, so triage crons and the
doer pipeline carry a single client instead of duplicating this logic.

kagent 1.0's ATE rewrite (~2026-11-10) removed the per-agent Deployment +
Service + raw-A2A-JSON-RPC model this client used to speak (`http://<agent>
.ai:8080/`, `message/send` / `tasks/get`). Agents now run as transient
gVisor-sandboxed Actors on a shared WorkerPool, and the only HTTP surface the
controller exposes for invoking one is its own built-in MCP server at
`kagent-controller.ai:8083/mcp` (Streamable HTTP; kagent-dev/kagent#1160 —
the same endpoint Claude Code uses via kagent-mcp.${SECRET_DOMAIN}/mcp). So
this client now speaks MCP (`tools/call` -> `invoke_agent`), not A2A directly
— kagent's own MCP server is itself the A2A front door.

Contract:
  1. POST `tools/call` / `invoke_agent` with the target agent + prompt.
  2. The controller blocks until the agent's task reaches a terminal state
     (observed empirically: no partial/non-terminal response, no separate
     poll step needed) and returns the result inline.

`call_agent` NEVER raises for an operational failure (transport error,
timeout, task failure). It returns `("", "<state>")` and lets the caller
decide whether that is a silent no-op (triage) or a logged skip (doer). This
is what turns a slow/misbehaving agent into a silent skipped run instead of a
KubeJobFailed page. Stdlib only — runs on the doer's python image and the
triage python image with nothing to pip-install.
"""
import json
import urllib.request

# Terminal MCP/A2A task states (after stripping any `task_state_` / `TASK_STATE_`
# prefix and lowercasing). `working`/`submitted`/`auth-required` would be the
# non-terminal ones, but kagent's invoke_agent tool call blocks until terminal,
# so in practice we only ever see one of these.
TERMINAL = {"completed", "failed", "canceled", "rejected", "unknown", "input-required"}


def _parse_jsonrpc(raw):
    """Body may be a plain JSON-RPC object OR an SSE stream of `data: {…}` lines
    (kagent's MCP endpoint advertises `text/event-stream`). Return the last JSON
    object."""
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


def _extract_text(tool_result):
    """kagent's invoke_agent MCP tool returns {"content":[{"type":"text",
    "text":...}], "structuredContent":{...,"text":...}, "isError":bool}. Prefer
    structuredContent.text (the clean final string); fall back to the content
    parts kagent always also populates."""
    sc = tool_result.get("structuredContent") or {}
    if sc.get("text"):
        return sc["text"]
    parts = [p.get("text", "") for p in (tool_result.get("content") or [])
             if p.get("type") == "text"]
    return parts[-1] if parts else ""


def _state_of(tool_result):
    sc = tool_result.get("structuredContent") or {}
    state = sc.get("state") or ""
    # kagent reports "TASK_STATE_FAILED" / "TASK_STATE_COMPLETED" etc.
    if state.lower().startswith("task_state_"):
        state = state[len("task_state_"):]
    state = state.lower()
    if state:
        return state
    return "failed" if tool_result.get("isError") else "?"


def call_agent(agent_mcp_url, agent_name, prompt, budget=600, poll_interval=10,
                message_id="local-cron", agent_namespace="ai"):
    """Send `prompt` to the kagent Agent `agent_name` (namespace `agent_namespace`)
    via the kagent controller's MCP endpoint at `agent_mcp_url`. Return
    (final_text, state) within `budget` s.

    Returns ("", state) on any operational failure — never raises for one.
    `poll_interval` is kept in the signature for call-site compatibility but is
    unused: invoke_agent blocks until the task is terminal, there is nothing to
    poll.
    """
    call = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "invoke_agent",
            "arguments": {
                "agent": {"namespace": agent_namespace, "name": agent_name},
                "message": prompt,
                "message_id": message_id,
            },
        },
    }
    try:
        d = _post(agent_mcp_url, call, timeout=max(30, budget))
    except Exception as e:
        return "", "transport-error: %r" % e

    if d.get("error"):
        # JSON-RPC-level failure (bad agent name/namespace, malformed call) —
        # never a backend/model issue, but still a non-productive terminal.
        return "", "transport-error: %r" % d["error"]

    result = d.get("result") or {}
    text = _extract_text(result)
    state = _state_of(result)
    return text, state


# Substrings that mean the agent's MODEL BACKEND was unreachable — the GPU-backed
# serving (Ollama on the P40, vLLM on the Spark) behind LiteLLM, or LiteLLM itself
# mid-roll — OR the agent's sandboxed Actor couldn't reach it/its MCP tools at the
# network layer (kagent 1.0 ATE EgressPolicy gap; see
# reference_kagent_ate_egresspolicy_blocks_all_agent_invocation in memory).
# Distinct from the model replying with garbage. Only consulted on a
# non-productive terminal state, so a healthy reply that happens to mention one of
# these words can't trip it (its state is `completed`, not `failed`).
_BACKEND_MARKERS = (
    "no deployments available", "connection refused", "stream_error",
    "apiconnectionerror", "service unavailable", "upstream connect error",
    "econnrefused", "litellm", ":4000", "403", "forbidden",
)


def is_backend_unavailable(text, state):
    """True when a run failed because the model backend (GPU) — or the sandbox's
    egress to it — was unreachable: the retryable 'missing GPU/egress, requeue and
    retry when available' case. Shared by the triage and doer crons so both
    requeue on the same signal."""
    # Transport failure reaching the controller/MCP endpoint itself.
    if state.startswith("transport-error"):
        return True
    # Agent reached but the task terminated 'failed': backend-down only if it
    # returned no text to classify, or its text names an upstream model/transport
    # error. A 'failed' task with a real (but untagged) reply is model drift, not a
    # GPU/egress outage — that stays `malformed` for the caller to classify.
    if state == "failed":
        low = (text or "").lower()
        if not low.strip():
            return True
        if any(m in low for m in _BACKEND_MARKERS):
            return True
    return False
