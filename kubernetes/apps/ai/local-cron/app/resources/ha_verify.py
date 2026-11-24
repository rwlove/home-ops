#!/usr/bin/env python3
"""Deterministic ground-truth backstop for the smart-home-triage watcher.

Opt-in (HA_VERIFY=true, set only on cronjob-smart-home-triage.yaml): before
the shared triage_run.py send-gate emails a PROBLEM body, every finding chunk
that names a Home Assistant entity_id is checked against live HA state, read
through one POST /api/template call.

Three classes of claim the local model has gotten wrong (2026-11-24 test
runs of the smart-home triage):

1. BATTERY ON A DEVICE WITH NO WORKING BATTERY SENSOR. "Check/replace the
   battery" was proposed for a hardwired in-wall paddle switch whose
   zwave-js-ui discovery entity had never held a value. A chunk that makes a
   battery claim is DROPPED when no device behind its entities has a battery
   sensor with a numeric state.

2. "DOWN" CLAIMS FOR ENTITIES THAT ARE UP. A chunk that says its entities are
   unavailable / unknown / offline is DROPPED when every named entity
   currently reports a normal state (it recovered, or the claim was wrong).

3. WRONG OUTAGE DURATIONS. A camera down ~1h was reported as "~6h". For a
   down-claim chunk whose first-named entity is still unavailable/unknown,
   the model's own "since ..." and "(~Nh)" figures are removed and replaced
   with the duration computed from the entity's last_changed.

FAIL-SAFE DIRECTION: HA_TOKEN unset, HA unreachable, a template error or an
unparseable reply -> the body is returned UNCHANGED. Chunks naming no
entity_id, or an entity HA does not know, pass through byte-for-byte.

Stdlib only -- no deps, matches the rest of this app.
"""
import datetime
import json
import os
import re
import urllib.request
from zoneinfo import ZoneInfo

import longhorn_verify  # reuse its markdown chunk splitter (same discipline)

TZ = ZoneInfo("America/New_York")
BAD_STATES = ("unavailable", "unknown")

_ENTITY = re.compile(
    r"\b((?:sensor|binary_sensor|camera|switch|light|lock|cover|climate|fan|"
    r"media_player|device_tracker|assist_satellite|alarm_control_panel|"
    r"water_heater|vacuum|number|select|button|update|event)\.[a-z0-9_]+)\b"
)
_BATTERY_CLAIM = re.compile(r"(?i)\bbatter(?:y|ies)\b")
_DOWN_CLAIM = re.compile(
    r"(?i)\b(unavailable|unknown|offline|not reporting|stopped reporting|"
    r"no (?:numeric )?reading|dropped (?:off|out)|went down|is down|dead)\b"
)
# The model's own duration figures, removed before the verified one is added.
_SINCE = re.compile(
    r"(?i)\s*\bsince\s+(?:~\s*)?\d{4}-\d{2}-\d{2}"
    r"(?:[ T]+\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?"
    r"(?:\s*(?:ET|EST|EDT|UTC|Z))?"
)
_PAREN_DUR = re.compile(
    r"(?i)\s*\((?:~|about |approx\.? |roughly )?\s*\d+(?:\.\d+)?\+?\s*"
    r"(?:h|hrs?|hours?|d|days?|m|mins?|minutes?)\)"
)

# One render returns facts for every entity the body names.
_TEMPLATE = r"""
{%- set ns = namespace(rows=[]) -%}
{%- for e in ids -%}
  {%- set s = states[e] -%}
  {%- if s is none -%}
    {%- set ns.rows = ns.rows + [{'id': e, 'exists': false}] -%}
  {%- else -%}
    {%- set d = device_id(e) -%}
    {%- set bn = namespace(ok=false, any=false) -%}
    {%- if d -%}
      {%- for x in device_entities(d) -%}
        {%- if state_attr(x, 'device_class') == 'battery' -%}
          {%- set bn.any = true -%}
          {%- if x.startswith('sensor.') and states(x) | is_number -%}
            {%- set bn.ok = true -%}
          {%- elif x.startswith('binary_sensor.')
                and states(x) in ['on', 'off'] -%}
            {%- set bn.ok = true -%}
          {%- endif -%}
        {%- endif -%}
      {%- endfor -%}
    {%- endif -%}
    {%- set ns.rows = ns.rows + [{
        'id': e, 'exists': true, 'state': s.state,
        'last_changed': s.last_changed.isoformat(),
        'has_device': d is not none,
        'battery_any': bn.any, 'battery_ok': bn.ok}] -%}
  {%- endif -%}
{%- endfor -%}
{{ ns.rows | to_json }}
"""


def _render(ids, timeout=15):
    url = os.environ.get("HA_URL", "http://home-assistant.home:8123").rstrip("/")
    token = os.environ["HA_TOKEN"]
    req = urllib.request.Request(
        url + "/api/template",
        data=json.dumps({"template": _TEMPLATE,
                         "variables": {"ids": ids}}).encode(),
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _fmt_duration(seconds):
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return "%dd %dh" % (days, hours)
    if hours:
        return "%dh %02dm" % (hours, mins)
    return "%dm" % mins


def _verified_since(fact, now):
    changed = datetime.datetime.fromisoformat(fact["last_changed"])
    local = changed.astimezone(TZ)
    return "%s since %s ET (%s)" % (
        fact["state"], local.strftime("%Y-%m-%d %H:%M"),
        _fmt_duration((now - changed).total_seconds()))


def _check_chunk(chunk, facts, now):
    """Return (new_chunk_or_None, reason). None = drop the chunk."""
    named = [e for e in dict.fromkeys(_ENTITY.findall(chunk))]
    known = [facts[e] for e in named if facts.get(e, {}).get("exists")]
    if not known:
        return chunk, None

    prose = _ENTITY.sub("", chunk)  # "battery" inside an entity_id is not a claim
    if _BATTERY_CLAIM.search(prose):
        devs = [f for f in known if f["has_device"]]
        if devs and not any(f["battery_ok"] for f in devs):
            return None, "battery claim, no device has a working battery sensor"

    if _DOWN_CLAIM.search(prose):
        if all(f["state"] not in BAD_STATES for f in known):
            return None, "down claim, every named entity reports a normal state"
        first = known[0]
        if first["state"] in BAD_STATES:
            stripped = _PAREN_DUR.sub("", _SINCE.sub("", chunk))
            tag = "[verified: %s %s]" % (first["id"],
                                         _verified_since(first, now))
            lines = stripped.split("\n")
            lines[0] = lines[0].rstrip() + " " + tag
            new = "\n".join(lines)
            if new != chunk:
                return new, "duration replaced from %s last_changed" % first["id"]
    return chunk, None


def verify(body, log=None, render=None, now=None):
    """Apply the HA backstops. On ANY failure the body is returned UNCHANGED."""
    try:
        ids = sorted(set(_ENTITY.findall(body)))
        if not ids:
            return body
        if render is None:
            if not os.environ.get("HA_TOKEN"):
                return body
            render = _render
        rows = render(ids)
        facts = {r["id"]: r for r in rows}
        now = now or datetime.datetime.now(datetime.timezone.utc)
        kept, changed = [], False
        for chunk in longhorn_verify._split_chunks(body):
            new, reason = _check_chunk(chunk, facts, now)
            if reason and log:
                log("ha-verify: %s -- %s" % (
                    "dropped chunk" if new is None else "rewrote chunk", reason))
            if new is None:
                changed = True
                continue
            changed = changed or new != chunk
            kept.append(new)
        if not changed:
            return body
        return "\n\n".join(kept).rstrip()
    except Exception as e:  # noqa: BLE001 -- a verify failure must not bury a finding
        if log:
            log("ha-verify: failed (%s) -- passing body through unchanged" % e)
        return body
