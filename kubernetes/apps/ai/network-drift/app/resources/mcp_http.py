#!/usr/bin/env python3
"""Minimal, stdlib-only MCP streamable-HTTP client (zero LLM tokens).

The continuous-NetBox-freshness sweep reads its sources through the per-backend,
single-purpose, read-only MCP services (netbox-mcp, omada-mcp, github-mcp) the
network-drift CronJob already uses — NOT the mcp-gateway broker (which has no
per-tool authz). Those services hold their own read-only credentials, so this
client carries NONE: it just speaks JSON-RPC over the Streamable-HTTP transport.

Transport (MCP 2025-06-18 Streamable HTTP):
  1. POST `initialize`           -> capture the `Mcp-Session-Id` response header
  2. POST `notifications/initialized` (with the session header)
  3. POST `tools/call`           -> parse the tool result

A response may come back as a single JSON object OR as an SSE `text/event-stream`
(one or more `data:` frames). Both are handled. A tool result's payload is the
text of the first `content` block of type `text`, which these servers return as a
JSON string — the caller `json.loads()` it.

Deterministic and side-effect free on the READ path: every tool call here is a
read-only get/list. Nothing in this module can mutate a backend.
"""
import json
import urllib.error
import urllib.request


class MCPError(RuntimeError):
    """A backend MCP call failed — surfaced by the sweep as a source finding."""


class MCPClient:
    def __init__(self, name, url, timeout=60):
        # name: human label for logs/findings (e.g. "omada"); url: the …/mcp endpoint.
        self.name = name
        self.url = url
        self.timeout = timeout
        self._session = None
        self._id = 0

    # -- transport ---------------------------------------------------------

    def _post(self, payload, extra_headers=None):
        headers = {
            "Content-Type": "application/json",
            # Accept BOTH so the server may answer with plain JSON or SSE.
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18",
        }
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(), headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                sid = r.headers.get("Mcp-Session-Id")
                if sid:
                    self._session = sid
                ctype = (r.headers.get("Content-Type") or "").lower()
                raw = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            raise MCPError("%s: HTTP %s %s" % (self.name, e.code, body))
        except Exception as e:  # noqa: BLE001 - any transport error is a finding
            raise MCPError("%s: transport error: %s" % (self.name, e))
        return ctype, raw

    @staticmethod
    def _parse(ctype, raw):
        # A notification (no id) yields an empty body — nothing to parse.
        if not raw.strip():
            return None
        if "text/event-stream" in ctype:
            # Concatenate the JSON carried across `data:` SSE frames; the last
            # complete JSON-RPC object is the response to our request.
            last = None
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    chunk = line[5:].strip()
                    if not chunk:
                        continue
                    try:
                        last = json.loads(chunk)
                    except ValueError:
                        continue
            return last
        return json.loads(raw)

    def _rpc(self, method, params=None):
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            payload["params"] = params
        ctype, raw = self._post(payload)
        msg = self._parse(ctype, raw)
        if msg is None:
            raise MCPError("%s: empty response to %s" % (self.name, method))
        if isinstance(msg, dict) and msg.get("error"):
            raise MCPError("%s: %s -> %s" % (self.name, method, msg["error"]))
        return msg.get("result") if isinstance(msg, dict) else None

    # -- lifecycle ---------------------------------------------------------

    def connect(self):
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "netbox-sync", "version": "1"},
            },
        )
        # `notifications/initialized` is a notification: no id, no response body.
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    # -- tools -------------------------------------------------------------

    def call(self, tool, arguments=None):
        """Call a read-only tool; return the parsed JSON payload (dict/list)."""
        result = self._rpc("tools/call", {"name": tool, "arguments": arguments or {}})
        if not isinstance(result, dict):
            raise MCPError("%s: tool %s returned no result" % (self.name, tool))
        if result.get("isError"):
            raise MCPError("%s: tool %s reported isError: %s"
                           % (self.name, tool, _first_text(result)))
        text = _first_text(result)
        if text is None:
            # Some servers put structured output directly under structuredContent.
            sc = result.get("structuredContent")
            if sc is not None:
                return sc
            raise MCPError("%s: tool %s returned no text content" % (self.name, tool))
        text = text.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            # A non-JSON string payload — hand it back verbatim.
            return text


def _first_text(result):
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            return block.get("text")
    return None
