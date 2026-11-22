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

Confirm-before-report: a failed Check B canary is retried ONCE in-run, after
CONFIRM_DELAY_S on a FRESH session, and only becomes a finding if the retry
also fails (per feedback_alert_only_on_sustained_not_transient). A single
pooled-connection reset (503 "connection termination" in ~0.1s), a one-off
upstream 504, or a momentary timeout self-heals and stays silent; a real
outage fails both attempts and still reports on the same run. Safe because
every canary is read-only by manifest rule.

Reporting: SILENT (exit 0, no email) on a full pass. Any finding sends ONE
notify-mcp email, one plain-English block per broken backend (what's broken,
who's affected, likely cause, where to look, raw error last), per
feedback_agent_notifications_must_be_actionable. A
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
CONFIRM_DELAY_S = 5  # wait before the one in-run confirm retry of a failed canary
# Cap on confirm retries per run: each costs up to ~CONFIRM_DELAY_S + INIT_TIMEOUT
# + HTTP_TIMEOUT, and a multi-backend outage must still report inside the
# Job's activeDeadlineSeconds (360; a clean run is ~2m). Past the cap, failures report unconfirmed.
MAX_CONFIRM_RETRIES = 4
_confirm_budget = [MAX_CONFIRM_RETRIES]


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------
# Plain-English rendering. Robert reads these emails cold, on a phone: every
# finding says WHAT is broken, WHO loses it, the LIKELY CAUSE, and WHERE TO
# LOOK -- the raw protocol error goes last, as reference only.
# --------------------------------------------------------------------------
CONSUMER_LABELS = {
    "kagent": "the kagent agents",
    "laptop": "Claude Code on the laptop",
    "open-webui": "Open WebUI chats",
}


def consumer_label(profile_name):
    return CONSUMER_LABELS.get(profile_name, profile_name)


def join_and(items):
    return items[0] if len(items) == 1 else "%s and %s" % (", ".join(items[:-1]), items[-1])


def backend_label(registration):
    return registration[:-len("-tools")] if registration.endswith("-tools") else registration


def explain_call_error(name, err):
    """(likely cause, where to look) for a failed canary call, by error class."""
    e = (err or "").lower()
    pods = "kubectl get pods -A | grep %s-mcp   (then: kubectl logs on that pod)" % name
    if "tool-reported error" in e:
        if "rate limit" in e:
            return ("the %s MCP server is up, but the service behind it is rate-limiting us." % name,
                    "usually clears on its own; only act if it repeats for hours.")
        return ("the %s MCP server is up, but the app behind it returned an error." % name,
                "check that the %s app itself is healthy and its API credentials still work; %s" % (name, pods))
    if "jsonrpc error" in e:
        return ("the gateway rejected the call -- a registration or tool-scoping problem, not the app.",
                "kubectl -n mcp-system get mcpserverregistration %s-tools -o yaml   (status/conditions)" % name)
    if "504" in e or "timed out" in e or "timeout" in e:
        return ("the %s MCP server did not answer in time -- usually the app behind it is slow or down." % name,
                pods)
    if "503" in e or "reset" in e or "connection" in e or "refused" in e:
        return ("the gateway could not reach the %s MCP server -- pod down/restarting, or a network "
                "policy blocking the gateway." % name, pods)
    return ("unclassified failure -- see the raw error below.", pods)


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


def close_session(token, path, vs_header, session_id):
    """Best-effort DELETE of a session we opened (MCP streamable-HTTP session
    termination). Without it every run abandoned 3+ sessions per profile on the
    broker and, through it, on each backend -- comfyui-mcp keeps every session
    it ever saw and OOMs (~87 sessions per ~6h). Failure here is logged, never
    a finding: it is cleanup, not a conformance signal."""
    if not session_id:
        return
    headers = {"Authorization": "Bearer %s" % token, "Mcp-Session-Id": session_id}
    if vs_header:
        headers["X-Mcp-Virtualserver"] = vs_header
    req = urllib.request.Request("https://%s%s" % (BROKER_HOST, path), headers=headers, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
    except urllib.error.HTTPError as e:
        if e.code not in (404, 405):  # already gone / server doesn't support DELETE
            log("WARN: session DELETE returned %s" % e.code)
    except Exception as e:
        log("WARN: session DELETE failed: %r" % e)


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
            name = backend_label(b["registration"])
            findings.append(
                "%s TOOLS HAVE DISAPPEARED\n"
                "  What's broken: none of the %s tools (%s*) show up in the gateway's tool list, "
                "so no consumer can see or call them.\n"
                "  Likely cause: the gateway dropped the registration during discovery -- known cause "
                "is a single-entry supportedProtocolVersions on the MCPServerRegistration "
                "(home-ops#15975/#16001).\n"
                "  Where to look: kubectl -n mcp-system get mcpserverregistration %s -o yaml"
                % (name.upper(), name, prefix, b["registration"])
            )
    return findings


# --------------------------------------------------------------------------
# Check B -- real tools/call, scoped to what the profile's OWN live
# tools/list actually contains.
# --------------------------------------------------------------------------
def confirm_retry(token, profile, tool, args, first_err):
    """One in-run confirm retry of a failed canary, on a fresh session so a
    stale pooled connection or wedged session can't fail it twice. Returns
    call_tool's (ok, elapsed, err); on a second failure err carries both."""
    if _confirm_budget[0] <= 0:
        return False, None, "%s (unconfirmed: confirm-retry budget for this run exhausted)" % first_err
    _confirm_budget[0] -= 1
    log("Check B [%s] %s first attempt failed (%s) -- confirming in %ds on a fresh session"
        % (profile["name"], tool, first_err, CONFIRM_DELAY_S))
    time.sleep(CONFIRM_DELAY_S)
    session_id, _, serr = mcp_session(token, profile["path"], profile.get("virtualserver"))
    if not session_id:
        return False, None, "%s; confirm retry could not open a session: %s" % (first_err, serr)
    # (tools/list errors on this fresh session don't matter -- we only need it to call one tool)
    ok, elapsed, err = call_tool(
        token, profile["path"], profile.get("virtualserver"), session_id, tool, args,
    )
    close_session(token, profile["path"], profile.get("virtualserver"), session_id)
    if ok:
        log("Check B [%s] %s recovered on confirm retry -- transient, not reported" % (profile["name"], tool))
        return True, elapsed, None
    return False, elapsed, "%s; confirm retry also failed: %s" % (first_err, err)


def canary_call_findings(token, profile, tools, backends, call_failures):
    live_names = {t.get("name", "") for t in tools}
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
        if not ok and b["registration"] not in call_failures:
            # Already confirmed broken via another consumer -- don't spend a retry re-proving it.
            ok, elapsed, err = confirm_retry(token, profile, tool, canary.get("args", {}), err)
        if ok:
            log("Check B [%s] %-24s %-35s OK  %.2fs" % (profile["name"], b["registration"], tool, elapsed))
        else:
            entry = call_failures.setdefault(b["registration"], {"tool": tool, "consumers": [], "errors": []})
            entry["consumers"].append(profile["name"])
            entry["errors"].append("[%s] after %.1fs: %s" % (profile["name"], elapsed or 0.0, err))
            log("Check B [%s] %-24s %-35s FAIL %s" % (profile["name"], b["registration"], tool, err))


def render_call_failures(call_failures):
    """One plain-English block per broken backend (not per consumer)."""
    findings = []
    for registration, f in sorted(call_failures.items()):
        name = backend_label(registration)
        cause, look = explain_call_error(name, f["errors"][0])
        findings.append(
            "%s TOOLS ARE FAILING\n"
            "  Who's affected: %s can't use the %s tools.\n"
            "  How we know: a read-only test call (%s) failed twice, %ds apart.\n"
            "  Likely cause: %s\n"
            "  Where to look: %s\n"
            "  Raw error: %s"
            % (name.upper(), join_and([consumer_label(c) for c in f["consumers"]]), name,
               f["tool"], CONFIRM_DELAY_S, cause, look, " | ".join(f["errors"]))
        )
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
                "(RBAC/connectivity issue in this checker, not a tool outage)" % e]

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
            "KAGENT AGENTS HAVE A STALE TOOL LIST\n"
            "  What's broken: the kagent agents only know about %d of the %d tools they should "
            "have, so the missing ones are invisible to them (everything else keeps working).\n"
            "  Likely cause: the kagent controller's tool-discovery cache froze (known bug, "
            "home-ops#15962).\n"
            "  Fix: bump spec.description in "
            "kubernetes/apps/ai/kagent/app/remotemcpserver-mcp-gateway.yaml and merge -- that "
            "forces re-discovery." % (discovered_count, declared)
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
    call_failures = {}  # registration -> {tool, consumers, errors}; rendered once per backend

    for profile in profiles:
        session_id, tools, err = mcp_session(token, profile["path"], profile.get("virtualserver"))
        if err or tools is None:
            findings.append(
                "%s CAN'T REACH THE TOOL GATEWAY\n"
                "  What's broken: every gateway tool is unavailable to %s.\n"
                "  Likely cause: the MCP gateway (or its virtual server for this consumer) is down "
                "or rejecting the login.\n"
                "  Where to look: kubectl -n mcp-system get pods | grep mcp-gateway\n"
                "  Raw error: %s (path=%s virtualserver=%s)"
                % (consumer_label(profile["name"]).upper(), consumer_label(profile["name"]), err,
                   profile["path"], profile.get("virtualserver"))
            )
            log("Check A [%s] SESSION FAILED: %s" % (profile["name"], err))
            close_session(token, profile["path"], profile.get("virtualserver"), session_id)
            continue

        log("Check A [%s]: session established, %d tools live" % (profile["name"], len(tools)))
        if profile["name"] == FULL_AGGREGATE_PROFILE:
            findings += prefix_presence_findings(profile["name"], tools, backends)

        profile["_session_id"] = session_id
        canary_call_findings(token, profile, tools, backends, call_failures)
        close_session(token, profile["path"], profile.get("virtualserver"), session_id)

    findings += render_call_failures(call_failures)
    findings += check_c_kagent_discovery_staleness()

    if findings:
        body = "\n\n".join(findings)
        log("FAIL -- %d finding(s)" % len(findings))
        log(body)
        if call_failures and len(findings) == len(call_failures):
            subject = "MCP tools failing: %s" % ", ".join(backend_label(r) for r in sorted(call_failures))
        else:
            subject = "MCP tool gateway: %d problem(s) need a look" % len(findings)
        try:
            notify_email(subject, body)
        except Exception as e:
            log("WARN: notify-mcp delivery failed: %r" % e)
        sys.exit(1)

    log("PASS -- %d consumer profile(s), %d backend(s), 0 findings. Silent (no email)." % (len(profiles), len(backends)))
    sys.exit(0)


if __name__ == "__main__":
    main()
