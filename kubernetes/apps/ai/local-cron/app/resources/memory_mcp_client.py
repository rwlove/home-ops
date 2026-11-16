#!/usr/bin/env python3
"""Minimal memory-mcp write client for the local-cron digest runners.

The weekly digests used to ask the agent (a local qwen) to write its own finding
to memory-mcp. The model was unreliable at it — it mis-targeted the entity (wrote
to a prior week's entity), duplicated observations, and computed inconsistent
entity names. This moves the write OUT of the model and into the runner: the
runner owns the entity name (a Python literal, never an LLM output) and writes
exactly one observation per run, idempotently.

Same transport as `a2a_client` / `memory-sync`'s ingest.py: raw MCP JSON-RPC over
streamable-http with the stdlib (no pip). Direct to the memory-mcp backend
Service (NOT through the mcp-gateway broker), so the tool names are memory-mcp's
NATIVE names (`get_entity`, `create_entity`, `add_observation`), not the
broker-federated `memory_*` names. No auth — memory-mcp has none configured and
is reachable only by pods Cilium lets through to :8070 (same as memory-sync).

Stdlib only; runs on the shared local-cron python image.
"""
import datetime
import json
import os
import re
import urllib.request

MCP_URL = os.environ.get("MEMORY_MCP_URL", "http://memory-mcp.mcp-system.svc.cluster.local:8070/mcp")
AGENT = "crusher-review"

_session = None
_rpc_id = 0


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _post(payload):
    """POST one JSON-RPC message; return the parsed {result|error} object (or None).

    Body may be plain JSON or an SSE stream of `data: {...}` lines — return the
    last object carrying a result/error. Captures/forwards Mcp-Session-Id if the
    server issues one (harmless under stateless mode)."""
    global _session
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if _session:
        headers["Mcp-Session-Id"] = _session
    req = urllib.request.Request(MCP_URL, data=data, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode("utf-8", "replace")
        sid = r.headers.get("Mcp-Session-Id")
    if sid:
        _session = sid
    out = None
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and ("result" in obj or "error" in obj):
            out = obj
    return out


def _call(tool, args):
    """tools/call wrapper. Returns (ok, payload) — payload is the text content or the error."""
    global _rpc_id
    _rpc_id += 1
    res = _post({"jsonrpc": "2.0", "id": _rpc_id, "method": "tools/call",
                 "params": {"name": tool, "arguments": args}})
    if not res:
        return False, "no response"
    if "error" in res:
        return False, res["error"]
    result = res.get("result", {})
    text = "".join(b.get("text", "") for b in result.get("content", []) if b.get("type") == "text")
    return True, text


def initialize():
    global _rpc_id
    _rpc_id += 1
    _post({"jsonrpc": "2.0", "id": _rpc_id, "method": "initialize",
           "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                      "clientInfo": {"name": AGENT, "version": "1"}}})
    _post({"jsonrpc": "2.0", "method": "notifications/initialized"})


def write_weekly_observation(entity_name, content, run_date, mcp_url=None):
    """Upsert exactly ONE observation onto a single, runner-owned entity.

    `entity_name` is a literal chosen by the runner — never the model — which is
    the whole fix: the LLM can no longer mis-target or mis-name the entity.

    Idempotent per `run_date`: the observation's source carries run_date, and a
    re-run on the same day is a no-op. So a manual test re-run and an at-least-once
    Job retry both leave exactly one observation for the week (no duplicates).

    Returns (ok, detail). Never raises for an operational memory-mcp failure the
    caller should treat as non-fatal — the caller already emailed the digest.
    """
    global MCP_URL
    if mcp_url:
        MCP_URL = mcp_url
    initialize()
    source = {"agent": AGENT, "at": _now_iso(), "run_date": run_date, "classification": "restricted"}

    gok, gpayload = _call("get_entity", {"name": entity_name})
    payload = gpayload if (gok and isinstance(gpayload, str)) else ""
    low = payload.lower()

    # Idempotency first, independent of existence detection: if this run_date is
    # already recorded, do nothing (covers same-day re-runs and Job retries).
    if re.search(r'"run_date"\s*:\s*"%s"' % re.escape(run_date), payload):
        return True, "already recorded for %s (idempotent no-op)" % run_date

    # Exists = a non-empty payload that names the entity and isn't a not-found
    # message (get_entity returns the entity JSON when present, an error/"not
    # found" otherwise). Not keyed on one JSON field name, which could drift.
    entity_exists = (
        bool(payload)
        and "not found" not in low
        and "does not exist" not in low
        and entity_name.lower() in low
    )
    if entity_exists:
        aok, ap = _call("add_observation", {
            "entity_name": entity_name, "content": content, "source": source})
        return (True, "appended observation") if aok else (False, "add_observation failed: %r" % (ap,))

    # Absent → create with the first observation.
    cok, _cp = _call("create_entity", {
        "name": entity_name, "type": "health_review", "namespace": "health",
        "observations": [content], "source": source})
    if cok:
        return True, "created entity + first observation"

    # create failed (exists after all / race) — append instead of duplicating.
    aok, ap = _call("add_observation", {
        "entity_name": entity_name, "content": content, "source": source})
    return (True, "created-fallback append") if aok else (False, "create+add failed: %r" % (ap,))
