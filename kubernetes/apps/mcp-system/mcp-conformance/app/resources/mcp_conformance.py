#!/usr/bin/env python3
"""mcp_conformance.py -- Phase 1 MCP federation conformance harness (mcp-platform).

Probes the Kuadrant mcp-gateway broker through the SAME public ingress path,
auth mechanism, and per-consumer X-Mcp-Virtualserver scoping that each real
consumer (kagent, laptop Claude, Open WebUI) actually uses -- never a
cluster-internal shortcut (docs/src/mcp_gateway_access_design.md Decision
1b). The whole point: "tools/list said healthy, tools/call was dead" was the
signature behind two real 2026-11-17 incidents, both invisible to CR status
and only found by rolling to prod and watching a real call fail. This job
automates that watch.

Three check types:

  Check A -- catalog scan (one tools/list per consumer profile). The prefix-presence sub-check (did a whole backend silently
    vanish from the served catalog -- the single-version
    supportedProtocolVersions regression class, home-ops#15975/#16001) only
    runs against the 'open-webui' profile, since it is the one profile with
    no VirtualServer scoping -- every registered backend's prefix MUST
    appear there. kagent/laptop intentionally curate a subset, so an absent
    prefix on those profiles is by design, not a finding. (A former
    schema-dialect sub-check -- flag any pre-2020-12 `$schema` because
    Claude Code dropped such tools, anthropics/claude-code#88882 -- was
    removed 2026-11-21: Claude Code 2.1.296 registers and calls draft-07
    tools, and the broker no longer strips the label.)

  Check B -- a real tools/call, one canary tool per backend (from
    canary-tools.yaml), against every consumer profile whose OWN live
    tools/list (from this same run's Check A) actually contains that tool.
    Every canary is read-only/idempotent by manifest convention -- see that
    file's header. This is the core check: it catches exactly the class of
    bug that hid behind green discovery twice in one day (the Router202511
    session hairpin and the inline-JSON-vs-SSE response stall).

  Check C -- kagent controller discovery staleness: the kagent-controller's
    cached RemoteMCPServer(ai/mcp-gateway).status.discoveredTools count vs
    the live MCPVirtualServer(mcp-system/kagent) declared tool count. A
    mismatch means the broker and virtualserver are fine but the
    controller's one-shot discovery cache is stale -- proven twice
    (2026-11-14, 2026-11-16) -- a class no protocol-level broker probe can
    ever catch, since the bug isn't in the broker.

Auth: mints its OWN short-lived bearer each run via the SAME Authelia
client-credentials flow the mcp-gateway-jwt-rotator CronJob uses (client_id/
secret from the mcp-gateway-authelia-client secret, scope mcp-gateway) --
authenticates as itself, never borrows a live consumer's token.

Reporting: SILENT (exit 0, no email) on a full pass. Any finding sends ONE
notify-mcp email naming the exact (profile, backend, tool) tuple + failure
signature, per feedback_agent_notifications_must_be_actionable. A
probe-infrastructure failure (can't mint a token, can't reach the broker at
all) is reported as its own distinct category so "the harness broke" never
reads as "the fleet is down."

Stdlib only -- mirrors local-cron's a2a_client.py/crusher_review.py
conventions (same no-deps, same notify-mcp /send contract). canary-tools.yaml
is valid JSON (kept as *.yaml for human skimmability) so it parses with the
stdlib json module -- no PyYAML dependency.
"""
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BROKER_HOST = os.environ["BROKER_HOST"]
AUTH_TOKEN_URL = os.environ["AUTH_TOKEN_URL"]
NOTIFY_URL = os.environ.get("NOTIFY_URL", "http://notify-mcp.mcp-system:8060/send")
CANARY_FILE = os.environ.get("CANARY_FILE", "/app/canary-tools.yaml")
AGENT_NAME = "mcp-platform"
HTTP_TIMEOUT = 15
INIT_TIMEOUT = 20
FULL_AGGREGATE_PROFILE = "open-webui"  # the one profile with no VS header


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------
# Auth -- mint our own bearer, same client-credentials flow as the broker's
# own JWT rotator (kubernetes/apps/mcp-system/mcp-gateway/app/rotator-cronjob.yaml).
# --------------------------------------------------------------------------
def mint_token():
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": os.environ["OIDC_CLIENT_ID"],
        "client_secret": os.environ["OIDC_CLIENT_SECRET"],
        "scope": "mcp-gateway",
        "audience": "mcp-gateway",
    }).encode()
    req = urllib.request.Request(AUTH_TOKEN_URL, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())["access_token"]


# --------------------------------------------------------------------------
# MCP JSON-RPC over streamable-http. Response body may be plain JSON or an
# SSE stream of `data: {...}` lines (same shape a2a_client.py already
# handles for A2A) -- the broker responds both ways depending on route.
# --------------------------------------------------------------------------
def _parse_jsonrpc(raw):
    raw = (raw or "").strip()
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


def _post(url, headers, payload, timeout):
    body = json.dumps(payload).encode()
    h = dict(headers)
    h["Content-Type"] = "application/json"
    h["Accept"] = "application/json, text/event-stream"
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode(errors="replace")
            recv_headers = {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        detail = e.read(300).decode(errors="replace") if hasattr(e, "read") else ""
        return None, None, time.monotonic() - t0, "http-error %s: %s" % (e.code, detail)
    except Exception as e:
        return None, None, time.monotonic() - t0, "transport-error: %r" % e
    return _parse_jsonrpc(raw), recv_headers, time.monotonic() - t0, None


def mcp_session(token, path, vs_header):
    """Open a fresh session against one consumer profile. Returns
    (session_id, tools, error) -- tools is the live tools/list result."""
    url = "https://%s%s" % (BROKER_HOST, path)
    headers = {"Authorization": "Bearer %s" % token}
    if vs_header:
        headers["X-Mcp-Virtualserver"] = vs_header
    init_payload = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2026-07-28",
            "capabilities": {},
            "clientInfo": {"name": "mcp-conformance-probe", "version": "1.0"},
        },
    }
    _, recv_headers, _, err = _post(url, headers, init_payload, INIT_TIMEOUT)
    if err:
        return None, None, "initialize failed: %s" % err
    session_id = (recv_headers or {}).get("mcp-session-id")
    if not session_id:
        return None, None, "initialize returned no Mcp-Session-Id header"
    headers["Mcp-Session-Id"] = session_id
    list_body, _, _, err = _post(url, headers, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, INIT_TIMEOUT)
    if err:
        return session_id, None, "tools/list failed: %s" % err
    tools = ((list_body or {}).get("result") or {}).get("tools") or []
    if not tools:
        return session_id, [], "tools/list returned zero tools"
    return session_id, tools, None


def call_tool(token, path, vs_header, session_id, tool, args):
    url = "https://%s%s" % (BROKER_HOST, path)
    headers = {"Authorization": "Bearer %s" % token, "Mcp-Session-Id": session_id}
    if vs_header:
        headers["X-Mcp-Virtualserver"] = vs_header
    d, _, elapsed, err = _post(
        url, headers,
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": tool, "arguments": args}},
        HTTP_TIMEOUT,
    )
    if err:
        return False, elapsed, err
    if not d:
        return False, elapsed, "empty/unparseable response body"
    if "error" in d:
        return False, elapsed, "jsonrpc error: %s" % json.dumps(d["error"])[:300]
    result = d.get("result") or {}
    if result.get("isError"):
        return False, elapsed, "tool-reported error: %s" % json.dumps(result)[:300]
    return True, elapsed, None


# --------------------------------------------------------------------------
# Check A -- prefix-presence (full-aggregate profile only).
# --------------------------------------------------------------------------
def prefix_presence_findings(profile_name, tools, backends):
    live_names = [t.get("name", "") for t in tools]
    findings = []
    for b in backends:
        prefix = b["prefix"]
        if not any(n.startswith(prefix) for n in live_names):
            findings.append(
                "Check A [%s]: backend '%s' (prefix '%s') has ZERO tools in the full-aggregate "
                "catalog -- it has silently vanished from tools/list entirely (not a call failure, "
                "a discovery failure). Known regression class: a single-entry "
                "supportedProtocolVersions on the MCPServerRegistration (home-ops#15975/#16001)."
                % (profile_name, b["registration"], prefix)
            )
    return findings


# --------------------------------------------------------------------------
# Check B -- real tools/call, scoped to what the profile's OWN live
# tools/list actually contains.
# --------------------------------------------------------------------------
def canary_call_findings(token, profile, tools, backends, last_known_good):
    live_names = {t.get("name", "") for t in tools}
    findings = []
    for b in backends:
        canary = b.get("canary")
        if not canary:
            continue
        tool = canary["tool"]
        if tool not in live_names:
            continue  # not scoped into this profile -- nothing to test here
        ok, elapsed, err = call_tool(
            token, profile["path"], profile.get("virtualserver"),
            profile["_session_id"], tool, canary.get("args", {}),
        )
        key = (profile["name"], b["registration"], tool)
        if ok:
            last_known_good[key] = elapsed
            log("Check B [%s] %-24s %-35s OK  %.2fs" % (profile["name"], b["registration"], tool, elapsed))
        else:
            lkg = last_known_good.get(key)
            lkg_str = ("%.2fs" % lkg) if lkg is not None else "never observed this run-set"
            findings.append(
                "Check B [%s] backend=%s tool=%s FAILED after %.1fs -- %s "
                "(last-known-good this run-set: %s)"
                % (profile["name"], b["registration"], tool, elapsed or 0.0, err, lkg_str)
            )
            log("Check B [%s] %-24s %-35s FAIL %s" % (profile["name"], b["registration"], tool, err))
    return findings


# --------------------------------------------------------------------------
# Check C -- kagent controller RemoteMCPServer discovery staleness vs the
# live MCPVirtualServer declared tool count. Pure k8s-API read, no HTTP to
# the broker -- this class lives one layer above it.
# --------------------------------------------------------------------------
def k8s_get(path):
    token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    with open(token_path) as f:
        tok = f.read().strip()
    ctx = ssl.create_default_context(cafile=ca_path)
    req = urllib.request.Request(
        "https://kubernetes.default.svc%s" % path,
        headers={"Authorization": "Bearer %s" % tok, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
        return json.loads(r.read().decode())


def check_c_kagent_discovery_staleness():
    try:
        rms = k8s_get("/apis/api.kagent.dev/v1alpha3/namespaces/ai/remotemcpservers/mcp-gateway")
        vs = k8s_get("/apis/mcp.kuadrant.io/v1/namespaces/mcp-system/mcpvirtualservers/kagent")
    except Exception as e:
        return ["Check C: could not read RemoteMCPServer(ai/mcp-gateway) or "
                "MCPVirtualServer(mcp-system/kagent) via the k8s API: %r "
                "(RBAC/connectivity issue, not a federation finding -- investigate separately)" % e]

    declared = len((vs.get("spec") or {}).get("tools") or [])
    conditions = (rms.get("status") or {}).get("conditions") or []
    message = conditions[-1].get("message", "") if conditions else ""
    discovered_count = (rms.get("status") or {}).get("discoveredTools")
    if isinstance(discovered_count, list):
        discovered_count = len(discovered_count)
    if discovered_count is None:
        m = re.search(r"(\d+)\s+MCP tools", message)
        discovered_count = int(m.group(1)) if m else None

    log("Check C: RemoteMCPServer(ai/mcp-gateway) discovered=%s declared=%s message=%r"
        % (discovered_count, declared, message[:120]))

    if discovered_count is None:
        return ["Check C: could not determine RemoteMCPServer(ai/mcp-gateway)'s discovered tool "
                "count from status.discoveredTools or the condition message (%r) -- CRD shape may "
                "have changed, update check_c_kagent_discovery_staleness()" % message[:200]]
    if discovered_count < declared:
        return [
            "Check C: kagent controller discovery is STALE -- RemoteMCPServer(ai/mcp-gateway) "
            "reports %d discovered tools but MCPVirtualServer(mcp-system/kagent) declares %d. "
            "The broker and virtualserver are fine; the controller's one-shot discovery cache "
            "froze at an earlier generation (known bug, home-ops#15962 saga). Workaround: bump "
            "remotemcpserver-mcp-gateway.yaml's spec.description to force metadata.generation and "
            "re-trigger discovery." % (discovered_count, declared)
        ]
    return []


# --------------------------------------------------------------------------
# notify-mcp
# --------------------------------------------------------------------------
def notify_email(subject, body):
    data = json.dumps({"subject": subject, "body": body, "agent": AGENT_NAME}).encode()
    req = urllib.request.Request(NOTIFY_URL, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.getcode()


def main():
    with open(CANARY_FILE) as f:
        manifest = json.load(f)
    profiles = manifest["consumer_profiles"]
    backends = manifest["backends"]

    try:
        token = mint_token()
    except Exception as e:
        body = "mcp-conformance could not mint a broker bearer via Authelia client-credentials: %r" % e
        log("FATAL: %s" % body)
        try:
            notify_email("mcp-conformance: PROBE INFRASTRUCTURE FAILURE (token mint)", body)
        except Exception as e2:
            log("WARN: notify-mcp delivery also failed: %r" % e2)
        sys.exit(1)

    findings = []
    last_known_good = {}  # (profile, backend, tool) -> last successful latency this run-set

    for profile in profiles:
        session_id, tools, err = mcp_session(token, profile["path"], profile.get("virtualserver"))
        if err or tools is None:
            findings.append(
                "Check A [%s]: could not establish a session / list tools at all -- %s "
                "(path=%s virtualserver=%s)"
                % (profile["name"], err, profile["path"], profile.get("virtualserver"))
            )
            log("Check A [%s] SESSION FAILED: %s" % (profile["name"], err))
            continue

        log("Check A [%s]: session established, %d tools live" % (profile["name"], len(tools)))
        if profile["name"] == FULL_AGGREGATE_PROFILE:
            findings += prefix_presence_findings(profile["name"], tools, backends)

        profile["_session_id"] = session_id
        findings += canary_call_findings(token, profile, tools, backends, last_known_good)

    findings += check_c_kagent_discovery_staleness()

    if findings:
        body = "%d finding(s):\n\n%s" % (len(findings), "\n\n".join(findings))
        log("FAIL -- %d finding(s)" % len(findings))
        log(body)
        try:
            notify_email(
                "mcp-conformance: %d finding(s) -- federation may be partially broken" % len(findings),
                body,
            )
        except Exception as e:
            log("WARN: notify-mcp delivery failed: %r" % e)
        sys.exit(1)

    log("PASS -- %d consumer profile(s), %d backend(s), 0 findings. Silent (no email)." % (len(profiles), len(backends)))
    sys.exit(0)


if __name__ == "__main__":
    main()
