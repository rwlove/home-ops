#!/usr/bin/env python3
"""One-way ingest: Claude's curated `.md` memory (materialized from the vault) ->
memory-mcp graph, so in-cluster kagent agents can `search` cluster knowledge.

- Source: the materialized vault at $VAULT_DIR (a livesync-cli sidecar puts the
  `claude` CouchDB DB there). We read $MEM_GLOB (this repo's memory notes).
- Sink: memory-mcp (STATEFUL streamable-HTTP MCP) at $MEMORY_MCP_URL. We speak
  raw JSON-RPC with the stdlib (no pip): initialize -> notifications/initialized
  -> tools/call. Each note becomes an entity (name = frontmatter `name`, type =
  metadata.type) with its body as an observation, stamped source.hash for
  idempotent re-runs (unchanged notes are skipped -> no re-embedding).

Runs read-only-ish: it only writes to memory-mcp, never to the vault or git.
Idempotent + safe to re-run. Ship the CronJob suspended; run once manually and
read the log before unsuspending.
"""
import glob
import hashlib
import json
import os
import re
import sys
import urllib.request

MCP_URL = os.environ.get("MEMORY_MCP_URL", "http://memory-mcp.mcp-system.svc.cluster.local:8070/mcp")
VAULT = os.environ.get("VAULT_DIR", "/vault")
MEM_GLOB = os.environ.get("MEM_GLOB", "projects/home-ops/memory/*.md")
AGENT = "memory-sync"
MAX_CONTENT = 8000  # cap per-note observation size

_session = None
_rpc_id = 0


def _post(payload):
    """POST one JSON-RPC message; return the parsed {result|error} object (or None)."""
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
    # Response is either plain JSON or SSE (`data: {...}` lines). Take the last
    # object that carries a result/error.
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
    """tools/call wrapper. Returns (ok, payload). payload is the text content or the error."""
    global _rpc_id
    _rpc_id += 1
    res = _post({"jsonrpc": "2.0", "id": _rpc_id, "method": "tools/call",
                 "params": {"name": tool, "arguments": args}})
    if not res:
        return False, "no response"
    if "error" in res:
        return False, res["error"]
    result = res.get("result", {})
    # MCP content blocks -> concatenated text
    text = "".join(b.get("text", "") for b in result.get("content", []) if b.get("type") == "text")
    return True, text


def initialize():
    global _rpc_id
    _rpc_id += 1
    _post({"jsonrpc": "2.0", "id": _rpc_id, "method": "initialize",
           "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                      "clientInfo": {"name": AGENT, "version": "1"}}})
    _post({"jsonrpc": "2.0", "method": "notifications/initialized"})


def parse_note(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    name = os.path.splitext(os.path.basename(path))[0]
    ntype, desc = "memory-note", ""
    body = text
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if m:
        fm, body = m.group(1), m.group(2)
        for line in fm.splitlines():
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip() or name
            elif line.startswith("description:"):
                desc = line.split(":", 1)[1].strip()
            else:
                tm = re.match(r"\s*type:\s*(user|feedback|project|reference)\b", line)
                if tm:
                    ntype = tm.group(1)
    return name, ntype, desc, body.strip()


def existing_hash(name):
    ok, payload = _call("get_entity", {"name": name})
    if not ok or not isinstance(payload, str):
        return None
    m = re.search(r'"hash"\s*:\s*"([0-9a-f]+)"', payload)
    return m.group(1) if m else None


def main():
    initialize()
    files = sorted(glob.glob(os.path.join(VAULT, MEM_GLOB)))
    files = [f for f in files
             if os.path.basename(f) != "MEMORY.md" and os.sep + "topics" + os.sep not in f]
    if not files:
        print(f"FATAL: no memory notes found under {VAULT}/{MEM_GLOB} "
              f"(is the vault materialized?)", file=sys.stderr)
        sys.exit(1)

    created = updated = skipped = failed = 0
    for f in files:
        name, ntype, desc, body = parse_note(f)
        if not body:
            continue
        h = hashlib.sha256(body.encode()).hexdigest()[:16]
        rel = os.path.relpath(f, VAULT)
        source = {"agent": AGENT, "context": rel, "hash": h}
        content = ((desc + "\n\n") if desc else "") + body
        content = content[:MAX_CONTENT]

        prior = existing_hash(name)
        if prior == h:
            skipped += 1
            continue

        # Absent or changed: try to create; if it already exists, update it +
        # add the fresh observation. (On a changed note this appends a new
        # observation rather than replacing — acceptable for a weekly sync;
        # bulk observation replacement is a v2 refinement.)
        ok, _ = _call("create_entity", {
            "name": name, "type": ntype, "namespace": "home-ops",
            "observations": [content], "source": source})
        if ok:
            created += 1
            continue

        uok, _ = _call("update_entity", {
            "name": name, "type": ntype, "namespace": "home-ops", "source": source})
        aok, apayload = _call("add_observation", {
            "entity_name": name, "content": content, "source": source})
        if uok and aok:
            updated += 1
        else:
            failed += 1
            print(f"WARN: create+update both failed for {name}: {apayload}", file=sys.stderr)

    print(f"memory-sync: {len(files)} notes | created={created} updated={updated} "
          f"skipped={skipped} failed={failed}")
    sys.exit(1 if failed and (created + updated) == 0 else 0)


if __name__ == "__main__":
    main()
