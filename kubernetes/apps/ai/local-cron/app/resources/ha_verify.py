#!/usr/bin/env python3
"""Deterministic ground-truth backstop for the smart-home-triage watcher.

Opt-in (HA_VERIFY=true, set only on cronjob-smart-home-triage.yaml): before
the shared triage_run.py send-gate emails a PROBLEM body, every finding chunk
that names a Home Assistant entity_id is checked against live HA state, read
through one POST /api/template call.

Five classes of claim the local model has gotten wrong (2026-11-24 test runs
and the 2026-11-27 production email of the smart-home triage):

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

4. CONFABULATED DISMISSALS (2026-11-27). A real 03:13-07:39 ET Z-Wave outage
   on the foyer Christmas tree was written off as "seasonal device being taken
   down post-holiday; not a fault. No action needed." A chunk containing
   dismissal reasoning (seasonal / holiday / taken down / not a fault / no
   action needed / expected unavailable / by design) is only allowed to keep
   it when the chunk names ONLY entities on the known-noise allowlist
   (_KNOWN_NOISE_*, mirroring the KNOWN/EXPECTED paragraphs in
   cronjob-smart-home-triage.yaml). Otherwise the dismissal sentences are
   REMOVED and a visible "[unverified dismissal removed ...]" line is appended,
   so the finding still reaches the human as a real problem.

5. HALLUCINATED ENTITY IDS (2026-11-27). The email cited
   sensor.kitchen_door_outside_kitchen_door_notification_power_maintenance_status;
   the real entity is ..._notification_power_management_battery_maintenance_status.
   Every entity_id in the body that HA does not know gets an inline
   " [unverified entity]" marker (allowlisted known-noise ids excepted -- those
   are often disabled and absent from states by design). The id is not
   guessed or substituted.

FAIL-SAFE DIRECTION: HA_TOKEN unset, HA unreachable, a template error or an
unparseable reply -> the body is returned UNCHANGED. Chunks naming no
entity_id pass through byte-for-byte unless they carry dismissal wording.

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
# Known-noise allowlist -- keep in sync with the KNOWN/EXPECTED, KNOWN FLAKY and
# UNAVAILABLE BY DESIGN paragraphs in cronjob-smart-home-triage.yaml. An entity
# is only allowed to be dismissed ("expected", "not a fault", ...) if it is here.
_KNOWN_NOISE_PREFIXES = (
    "sensor.pool_controller_",
    "binary_sensor.pool_controller_",
    "sensor.outside_pool_controller_pool_pump_energy",
    "device_tracker.whisperflo_pool_pump",
    "sensor.weather_station_temperature_2",   # winterized pool temp probe (+ battery)
    "sensor.pro_check_universal_a2dc_battery",
    "sensor.propane_percentage",
    "camera.reolink_frontdoor",
    "binary_sensor.openplantbook_upload_problem",
    "media_player.home_assistant_voice_",     # Voice PE Music Assistant mirrors
)
# For a chunk that names no entity_id at all: these name-fragments stand in for
# the allowlist.
_KNOWN_NOISE_WORDS = re.compile(
    r"(?i)pool[ _]controller|whisperflo|pool (?:pump|temperature|temp probe|light)|"
    r"propane|pro check|openplantbook|front[- ]?door camera|reolink[_ ]frontdoor|"
    r"voice pe|sendspin|music assistant mirror"
)
# Dismissal reasoning. Deliberately narrow: wording the model used to write off a
# real outage, not general prose.
_DISMISSAL = re.compile(
    r"(?i)\b(?:seasonal(?:ly)?|(?:post|after)[- ]?(?:the[- ])?holidays?|holidays?|"
    r"(?:being |been |is |are )?(?:taken|take|took) down|not (?:a|an) (?:fault|problem|issue)|"
    r"no (?:further )?action (?:is )?(?:needed|required|necessary)|"
    r"expected (?:to be )?(?:unavailable|offline|down|off|behaviou?r)|by design)\b"
)
_SENT_SPLIT = re.compile(r"(?<=[.!?;])\s+")
_UNVERIFIED = " [unverified entity]"

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


def _known_noise(entity_id):
    return entity_id.startswith(_KNOWN_NOISE_PREFIXES)


def _mark_unverified(chunk, facts):
    """Append _UNVERIFIED after every entity_id HA does not know (idempotent).
    Known-noise ids and glob stems ("sensor.pool_controller_*") are left alone."""
    def sub(m):
        eid = m.group(1)
        rest = chunk[m.end():m.end() + len(_UNVERIFIED)]
        if (eid.endswith("_") or _known_noise(eid) or rest == _UNVERIFIED
                or facts.get(eid, {}).get("exists", True)):
            return m.group(0)
        return m.group(0) + _UNVERIFIED
    return _ENTITY.sub(sub, chunk)


def _strip_dismissals(chunk, facts):
    """Remove dismissal sentences from a chunk that is not backed by the
    known-noise allowlist and append a visible flag. Returns the new chunk, or
    the chunk unchanged when no dismissal applies."""
    prose = _ENTITY.sub("", chunk)
    if not _DISMISSAL.search(prose):
        return chunk
    named = list(dict.fromkeys(_ENTITY.findall(chunk)))
    if named:
        unbacked = [e for e in named if not _known_noise(e)]
    else:
        unbacked = [] if _KNOWN_NOISE_WORDS.search(chunk) else ["(no entity named)"]
    if not unbacked:
        return chunk
    out = []
    for line in chunk.split("\n"):
        parts = _SENT_SPLIT.split(line)
        kept = [p for p in parts if not _DISMISSAL.search(_ENTITY.sub("", p))]
        out.append(" ".join(kept))
    stripped = "\n".join(l for l in out if l.strip())
    flag = ("[unverified dismissal removed: %s not on the known-noise list -- "
            "treat as a real problem and check it]" % ", ".join(unbacked[:3]))
    return stripped.rstrip() + "\n  " + flag


def _check_chunk(chunk, facts, now):
    """Return (new_chunk_or_None, reason). None = drop the chunk."""
    reasons = []
    work = _mark_unverified(chunk, facts)
    if work != chunk:
        reasons.append("unknown entity_id marked unverified")

    named = [e for e in dict.fromkeys(_ENTITY.findall(work))]
    known = [facts[e] for e in named if facts.get(e, {}).get("exists")]

    if known:
        prose = _ENTITY.sub("", work)  # "battery" inside an entity_id is not a claim
        if _BATTERY_CLAIM.search(prose):
            devs = [f for f in known if f["has_device"]]
            if devs and not any(f["battery_ok"] for f in devs):
                return None, "battery claim, no device has a working battery sensor"

        if _DOWN_CLAIM.search(prose):
            if all(f["state"] not in BAD_STATES for f in known):
                return None, "down claim, every named entity reports a normal state"
            first = known[0]
            if first["state"] in BAD_STATES:
                stripped = _PAREN_DUR.sub("", _SINCE.sub("", work))
                tag = "[verified: %s %s]" % (first["id"],
                                             _verified_since(first, now))
                lines = stripped.split("\n")
                lines[0] = lines[0].rstrip() + " " + tag
                new = "\n".join(lines)
                if new != work:
                    work = new
                    reasons.append("duration replaced from %s last_changed"
                                   % first["id"])

    after = _strip_dismissals(work, facts)
    if after != work:
        work = after
        reasons.append("dismissal not backed by known-noise list removed")

    if work == chunk:
        return chunk, None
    return work, "; ".join(reasons)


def verify(body, log=None, render=None, now=None):
    """Apply the HA backstops. On ANY failure the body is returned UNCHANGED."""
    try:
        ids = sorted(set(_ENTITY.findall(body)))
        if ids:
            if render is None:
                if not os.environ.get("HA_TOKEN"):
                    return body
                render = _render
            rows = render(ids)
        else:
            rows = []  # no entity to look up; the dismissal check is still local
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
