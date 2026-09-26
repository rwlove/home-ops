#!/usr/bin/env python3
"""agent-callback — turns a clicked email action button into an agent action.

The human-in-the-loop back-channel for the agent fleet. An agent email (rendered
by notify-mcp) can carry action buttons whose URLs point here with a signed,
short-lived token. Flow:

  Rob clicks a button in the email
    -> GET /act?t=<token>   (Authelia gates this route; the GET is SIDE-EFFECT-FREE
                             — it only verifies the token and renders a confirm page)
    -> Rob clicks "Confirm"
    -> POST /act            (marks the token used, then invokes the target agent
                             over A2A with the decision)

Why this shape:
  * The GET has NO side effects, so an email client that prefetches/scans the link
    (Gmail does) cannot trigger the action — it just renders a confirm page, and
    that page is Authelia-gated anyway (a prefetch has no session -> login wall).
  * The action only fires on an explicit POST from an Authelia-authenticated Rob.
  * Tokens are HMAC-signed (integrity), time-boxed (exp), and single-use (jti).
    notify-mcp holds the same HMAC key and signs the button URLs.

Auth is enforced at the gateway (Authelia SecurityPolicy on the HTTPRoute), so
this service trusts that anyone reaching it is Rob. The token signing prevents a
signed-in user from tampering the agent/action in the URL.

Env:
  CALLBACK_HMAC_KEY   REQUIRED — shared secret; notify-mcp signs, this verifies.
  A2A_BASE            default http://%s.ai:8080/  (%s = agent name)
  A2A_TIMEOUT         default 20 (seconds; the invoke is fire-and-ack)
  HOST / PORT         bind (default 0.0.0.0:8080)
Stdlib only.
"""
import hashlib
import hmac
import html
import json
import os
import time
import urllib.parse
import urllib.request
from base64 import urlsafe_b64decode
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))
HMAC_KEY = os.environ.get("CALLBACK_HMAC_KEY", "").encode()
A2A_BASE = os.environ.get("A2A_BASE", "http://%s.ai:8080/")
A2A_TIMEOUT = int(os.environ.get("A2A_TIMEOUT", "20"))

# In-memory single-use ledger of consumed token ids. Best-effort: Authelia is the
# real gate (only Rob can POST), and tokens are short-lived, so a restart forgetting
# used jtis is an acceptable, low-risk window rather than a correctness hole.
_USED: dict[str, float] = {}
_AGENT_OK = set("abcdefghijklmnopqrstuvwxyz0123456789-")


def log(msg: str) -> None:
    print("agent-callback: %s" % msg, flush=True)


def _b64d(s: str) -> bytes:
    return urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify(token: str) -> dict | None:
    """Return the token payload if the signature is valid and unexpired, else None."""
    if not HMAC_KEY or not token or token.count(".") != 1:
        return None
    body_b64, sig_b64 = token.split(".", 1)
    expected = hmac.new(HMAC_KEY, body_b64.encode(), hashlib.sha256).digest()
    try:
        got = _b64d(sig_b64)
    except Exception:
        return None
    if not hmac.compare_digest(expected, got):
        return None
    try:
        payload = json.loads(_b64d(body_b64))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if float(payload.get("exp", 0)) < time.time():
        return None
    agent = str(payload.get("a", ""))
    if not agent or any(c not in _AGENT_OK for c in agent):
        return None
    return payload


def _prune() -> None:
    now = time.time()
    for jti, exp in list(_USED.items()):
        if exp < now:
            _USED.pop(jti, None)


def invoke_agent(agent: str, action: str, ctx: str) -> None:
    """Fire the decision at the target agent over A2A (fire-and-ack)."""
    prompt = (
        "Robert CONFIRMED the following action via an email action button: "
        f"\"{action}\". Context: {ctx or '(none)'}. This is his explicit approval "
        "— proceed within your capabilities and, if the action needs a step you "
        "cannot perform yourself, prepare it and say exactly what remains. Then "
        "notify Robert of the outcome via your notify_rob tool."
    )
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "message/send",
        "params": {"message": {
            "role": "user",
            "parts": [{"kind": "text", "text": prompt}],
            "messageId": "email-callback", "kind": "message",
        }},
    }).encode()
    req = urllib.request.Request(A2A_BASE % agent, data=body, headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    })
    with urllib.request.urlopen(req, timeout=A2A_TIMEOUT) as r:
        r.read()


# --------------------------------------------------------------------------- #
# minimal themed pages (match the notify-mcp email look)
# --------------------------------------------------------------------------- #
def _page(title: str, accent: str, heading: str, body_html: str) -> bytes:
    return (f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title></head>
<body style="margin:0;background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif">
<div style="max-width:520px;margin:8vh auto;padding:0 16px">
<div style="background:#161b22;border:1px solid #30363d;border-radius:14px;overflow:hidden">
<div style="height:4px;background:{accent}"></div>
<div style="padding:26px 28px">
<div style="color:{accent};font-size:20px;font-weight:800;margin:0 0 10px">{html.escape(heading)}</div>
{body_html}
</div></div>
<div style="color:#6e7681;font-size:11px;text-align:center;margin-top:14px">agent-callback · kagent fleet</div>
</div></body></html>""").encode()


def confirm_page(token: str, p: dict) -> bytes:
    agent = html.escape(str(p.get("a", "")))
    action = html.escape(str(p.get("act", "")))
    ctx = html.escape(str(p.get("ctx", "")))
    label = html.escape(str(p.get("lbl", "Confirm")))
    accent = "#f0a020"
    body = (
        f'<p style="font-size:14px;line-height:1.55;color:#c9d1d9">You are about to confirm this action to '
        f'<b style="color:#e6edf3">{agent}</b>:</p>'
        f'<div style="background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:12px 14px;margin:10px 0">'
        f'<div style="color:#e6edf3;font-size:15px;font-weight:600">{action}</div>'
        + (f'<div style="color:#8b949e;font-size:13px;margin-top:6px">{ctx}</div>' if ctx else "")
        + "</div>"
        f'<form method="POST" action="/act" style="margin:16px 0 0">'
        f'<input type="hidden" name="t" value="{html.escape(token)}">'
        f'<button type="submit" style="display:inline-block;padding:11px 22px;border:0;border-radius:8px;'
        f'font-size:15px;font-weight:700;color:#0d1117;background:{accent};cursor:pointer">{label}</button>'
        f'</form>'
        f'<p style="font-size:12px;color:#6e7681;margin:14px 0 0">This link is single-use and expires. '
        f'Nothing happens until you press the button above.</p>'
    )
    return _page("Confirm action", accent, "Confirm action", body)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            self._send(200, b"ok", "text/plain")
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/act":
            self._send(404, _page("Not found", "#8b949e", "Not found", "<p>Nothing here.</p>"))
            return
        token = urllib.parse.parse_qs(parsed.query).get("t", [""])[0]
        p = verify(token)
        if not p:
            self._send(400, _page("Link invalid", "#e5534b", "Link invalid or expired",
                       "<p style='font-size:14px;color:#c9d1d9'>This action link is invalid, tampered, or "
                       "past its expiry. Ask the agent to send a fresh one.</p>"))
            return
        # GET is side-effect-free: just render the confirm page.
        self._send(200, confirm_page(token, p))

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/act":
            self._send(404, _page("Not found", "#8b949e", "Not found", "<p>Nothing here.</p>"))
            return
        n = int(self.headers.get("Content-Length", "0"))
        form = urllib.parse.parse_qs(self.rfile.read(n).decode()) if n else {}
        token = form.get("t", [""])[0]
        p = verify(token)
        if not p:
            self._send(400, _page("Link invalid", "#e5534b", "Link invalid or expired",
                       "<p style='font-size:14px;color:#c9d1d9'>This action link is invalid or expired.</p>"))
            return
        _prune()
        jti = str(p.get("jti", ""))
        if not jti or jti in _USED:
            self._send(409, _page("Already used", "#d4a017", "Already actioned",
                       "<p style='font-size:14px;color:#c9d1d9'>This action was already confirmed. "
                       "Nothing was done a second time.</p>"))
            return
        _USED[jti] = float(p.get("exp", time.time() + 3600))
        agent = str(p.get("a", ""))
        action = str(p.get("act", ""))
        ctx = str(p.get("ctx", ""))
        try:
            invoke_agent(agent, action, ctx)
        except Exception as e:  # noqa: BLE001
            log("A2A invoke failed for %s: %r" % (agent, e))
            self._send(502, _page("Couldn't reach the agent", "#e5534b", "Agent unreachable",
                       f"<p style='font-size:14px;color:#c9d1d9'>Confirmed, but {html.escape(agent)} could not "
                       "be reached to carry it out. It's safe to click Confirm again.</p>"))
            _USED.pop(jti, None)  # allow retry
            return
        log("confirmed -> %s: %s" % (agent, action))
        self._send(200, _page("Confirmed", "#3fb950", "Sent to " + html.escape(agent),
                   f"<p style='font-size:14px;color:#c9d1d9'>Your approval was delivered to "
                   f"<b style='color:#e6edf3'>{html.escape(agent)}</b>. It will carry it out and email you "
                   "the outcome. You can close this tab.</p>"))

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    if not HMAC_KEY:
        log("WARNING: CALLBACK_HMAC_KEY is empty — every token will be rejected until it is set")
    log("listening on :%d" % PORT)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
