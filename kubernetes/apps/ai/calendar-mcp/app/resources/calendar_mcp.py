#!/usr/bin/env python3
"""calendar-mcp — guarded Google Calendar write tools for the in-cluster kagent tier.

One shared "Concierge" calendar (Robert's personal Google account), written by a
service account. Mirrors the laptop `_shared/gcal.py` surface so the 24/7 kagent
personas can post events too. Credentials arrive via env from the ExternalSecret
`calendar-mcp-secret` (GOOGLE_SA_KEY = SA JSON, CALENDAR_ID). Guards:
  - DRY_RUN gate (hard floor): when "true", writes are described, not made.
  - per-process hourly write cap (MAX_WRITES_PER_HOUR) — blunts a runaway loop.
  - per-persona ownership: an agent may only edit/delete events it tagged.
No medical/restricted content lives here; event bodies are supplied by the caller
and must follow the same data-classification rules as the laptop path.
"""
import datetime as dt
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from mcp.server.fastmcp import FastMCP

TZ = "America/New_York"
SCOPES = ["https://www.googleapis.com/auth/calendar"]
API = "https://www.googleapis.com/calendar/v3"

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
MAX_WRITES_PER_HOUR = int(os.environ.get("MAX_WRITES_PER_HOUR", "50"))
CAL_ID = os.environ["CALENDAR_ID"]
_SA_INFO = json.loads(os.environ["GOOGLE_SA_KEY"])

_creds = service_account.Credentials.from_service_account_info(_SA_INFO, scopes=SCOPES)
_lock = threading.Lock()
_writes: list[float] = []  # timestamps of recent writes (sliding 1h window)


def _token() -> str:
    with _lock:
        if not _creds.valid:
            _creds.refresh(Request())
        return _creds.token


def _api(method: str, path: str, body=None, ok404: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {_token()}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if ok404 and e.code in (404, 410):
            return None
        raise RuntimeError(
            f"calendar API {method} {path.split('?')[0]} -> {e.code} {e.read().decode()[:300]}"
        )


def _cal(path: str = "") -> str:
    return f"/calendars/{urllib.parse.quote(CAL_ID, safe='')}{path}"


def _rate_ok() -> bool:
    now = time.time()
    with _lock:
        _writes[:] = [t for t in _writes if now - t < 3600]
        if len(_writes) >= MAX_WRITES_PER_HOUR:
            return False
        _writes.append(now)
        return True


def _when(value: str, all_day: bool) -> dict:
    return {"date": value} if all_day else {"dateTime": value, "timeZone": TZ}


def _persona(p: str) -> str:
    return (p or "").strip().lower()


def _owns(ev: dict, persona: str) -> bool:
    return ev.get("extendedProperties", {}).get("private", {}).get("persona", "") == persona


mcp = FastMCP("calendar-mcp", host="0.0.0.0", port=8080)


@mcp.tool()
def list_events(days: int = 30, persona: str = "") -> list:
    """List upcoming events on the shared Concierge calendar.

    days: look-ahead window (default 30). persona: optional filter to one
    persona's own events (crusher/cooter/warren/gordon/arnold).
    """
    now = dt.datetime.now(dt.timezone.utc)
    q = urllib.parse.urlencode({
        "timeMin": now.isoformat(),
        "timeMax": (now + dt.timedelta(days=days)).isoformat(),
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": "250",
    })
    want = _persona(persona)
    out = []
    for ev in (_api("GET", _cal(f"/events?{q}")) or {}).get("items", []):
        who = ev.get("extendedProperties", {}).get("private", {}).get("persona", "-")
        if want and who != want:
            continue
        s = ev.get("start", {})
        out.append({
            "id": ev["id"],
            "start": s.get("dateTime") or s.get("date"),
            "persona": who,
            "summary": ev.get("summary", ""),
        })
    return out


@mcp.tool()
def add_event(persona: str, title: str, start: str, end: str = "",
              description: str = "", all_day: bool = False) -> dict:
    """Add an event to the shared Concierge calendar.

    persona: owning agent (crusher/cooter/warren/gordon/arnold) — the title is
    auto-prefixed [Persona] and the event is tagged so only that persona can
    later edit/delete it. start/end are ISO naive Eastern (e.g.
    2026-11-03T14:00:00); for all_day pass a date (2026-11-03) and set all_day.
    Keep bodies free of secrets / media names / anyone else's private data.
    """
    p = _persona(persona)
    if not p:
        return {"error": "persona is required"}
    if not title or not start:
        return {"error": "title and start are required"}
    body = {
        "summary": f"[{p.capitalize()}] {title}",
        "description": description,
        "start": _when(start, all_day),
        "end": _when(end or start, all_day),
        "extendedProperties": {"private": {"persona": p}},
    }
    if DRY_RUN:
        return {"dry_run": True, "would_create": body["summary"], "start": start}
    if not _rate_ok():
        return {"error": f"write cap reached ({MAX_WRITES_PER_HOUR}/hour) — refusing"}
    ev = _api("POST", _cal("/events"), body)
    return {"created": ev["id"], "summary": ev.get("summary"), "link": ev.get("htmlLink")}


@mcp.tool()
def update_event(persona: str, event_id: str, title: str = "", start: str = "",
                 end: str = "", description: str = "", all_day: bool = False) -> dict:
    """Update an event the SAME persona created. Only supplied fields change."""
    p = _persona(persona)
    ev = _api("GET", _cal(f"/events/{urllib.parse.quote(event_id)}"), ok404=True)
    if ev is None:
        return {"error": "event not found"}
    if not _owns(ev, p):
        return {"error": "refused: event belongs to a different persona"}
    body: dict = {}
    if title:
        body["summary"] = f"[{p.capitalize()}] {title}"
    if description:
        body["description"] = description
    if start:
        body["start"] = _when(start, all_day)
    if end:
        body["end"] = _when(end, all_day)
    if not body:
        return {"error": "nothing to update"}
    if DRY_RUN:
        return {"dry_run": True, "would_update": event_id, "fields": list(body)}
    if not _rate_ok():
        return {"error": f"write cap reached ({MAX_WRITES_PER_HOUR}/hour) — refusing"}
    out = _api("PATCH", _cal(f"/events/{urllib.parse.quote(event_id)}"), body)
    return {"updated": out["id"]}


@mcp.tool()
def delete_event(persona: str, event_id: str) -> dict:
    """Delete an event the SAME persona created."""
    p = _persona(persona)
    ev = _api("GET", _cal(f"/events/{urllib.parse.quote(event_id)}"), ok404=True)
    if ev is None:
        return {"error": "event not found"}
    if not _owns(ev, p):
        return {"error": "refused: event belongs to a different persona"}
    if DRY_RUN:
        return {"dry_run": True, "would_delete": event_id}
    if not _rate_ok():
        return {"error": f"write cap reached ({MAX_WRITES_PER_HOUR}/hour) — refusing"}
    _api("DELETE", _cal(f"/events/{urllib.parse.quote(event_id)}"), ok404=True)
    return {"deleted": event_id}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
