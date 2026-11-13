#!/usr/bin/env python3
"""Deterministic Longhorn-backup-recency backstop for the storage-triage watcher.

Opt-in (LONGHORN_VERIFY_BACKUPS=true, set only on cronjob-storage-triage.yaml):
before the shared triage_run.py send-gate emails a PROBLEM body, this checks
each Longhorn volume name the LOCAL MODEL NAMED in its reply against ground
truth -- the Volume CR's own status.lastBackupAt (or, if that's empty, its
most recent Completed Backup CR) versus ~1.5x the fastest backup-task
RecurringJob cadence covering that volume's recurring-job-group labels. A
volume POSITIVELY confirmed current is dropped from the body -- the chunk
that named it, and ONLY that chunk; everything else is left untouched.

Why: prompt-steering a 7B model is the wrong layer for this specific false
positive (home-ops#15872) -- it kept re-flagging medialyze-config-xfs, a
ks-disabled app's RETAINED Longhorn volume that is still actively backed up
on schedule, as an orphan. See reference_longhorn_ks_disabled_app_pv_absent_
not_orphan in memory.

FAIL-SAFE DIRECTION: this module can only ever REMOVE a chunk it has
POSITIVELY verified as protected. Any ambiguity -- the Longhorn API
unreachable, RBAC denied, a volume not found, an unparseable timestamp, a
recurring-job-group label with no matching backup-task RecurringJob, a
chunk naming more than one volume -- leaves that chunk UNTOUCHED. A verify
failure must never bury a real finding; it can only fail to suppress a
false one, which is exactly today's (noisy, never-muting) behavior.

Stdlib only -- no deps, matches the rest of this app.
"""
import datetime
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

_SA = "/var/run/secrets/kubernetes.io/serviceaccount"
_API = "/apis/longhorn.io/v1beta2/namespaces/longhorn-system"


def _ctx():
    return ssl.create_default_context(cafile=os.path.join(_SA, "ca.crt"))


def _token():
    with open(os.path.join(_SA, "token"), encoding="utf-8") as f:
        return f.read().strip()


def _get(path, timeout=8):
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    url = "https://%s:%s%s" % (host, port, path)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer %s" % _token()})
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _parse_rfc3339(s):
    # Longhorn emits "2026-11-13T00:00:22Z" -- the only shape observed live.
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc)


# Cron-shape -> interval-days heuristic, scoped to the shapes actually used
# in kubernetes/apps/longhorn-system/longhorn/config/recurring-jobs.yaml
# (dom=day-of-month, dow=day-of-week):
#   "0 0 * * *"  (dom=*, dow=*)      -> daily
#   "0 0 * * 5"  (dom=*, dow fixed)  -> weekly
#   "0 0 1 * *"  (dom fixed, mon=*)  -> monthly (~30d)
# NOT a general cron-interval parser -- a cadence outside this shape returns
# None (unresolvable), which the caller treats as "can't confirm" (fail open).
def _cron_interval_days(cron):
    try:
        _, _, dom, month, dow = cron.split()
    except ValueError:
        return None
    if dom == "*" and month == "*" and dow == "*":
        return 1
    if dom == "*" and month == "*" and dow != "*":
        return 7
    if dom != "*" and month == "*":
        return 30
    return None


def _backup_task_group_intervals():
    """group-label-value -> fastest backup-task RecurringJob interval (days)
    covering it. Only spec.task == 'backup' counts -- snapshot/filesystem-
    trim cadence says nothing about offsite backup recency."""
    out = {}
    data = _get(_API + "/recurringjobs")
    for item in data.get("items", []):
        spec = item.get("spec", {})
        if spec.get("task") != "backup":
            continue
        days = _cron_interval_days(spec.get("cron", ""))
        if days is None:
            continue
        for group in spec.get("groups", []) or []:
            out[group] = min(days, out.get(group, days))
    return out


def _volume_names():
    data = _get(_API + "/volumes")
    return [i["metadata"]["name"] for i in data.get("items", [])
            if i.get("metadata", {}).get("name")]


def _last_backup_at(name):
    """status.lastBackupAt if set; else the newest Completed Backup CR's
    backupCreatedAt for this volume (belt-and-suspenders -- matches the
    storage-triage PROMPT's own instruction to check either source)."""
    vol = _get(_API + "/volumes/%s" % urllib.parse.quote(name))
    at = vol.get("status", {}).get("lastBackupAt")
    labels = vol.get("metadata", {}).get("labels", {}) or {}
    if not at:
        sel = urllib.parse.quote("backup-volume=%s" % name)
        data = _get(_API + "/backups?labelSelector=%s" % sel)
        completed = [b["status"]["backupCreatedAt"]
                     for b in data.get("items", [])
                     if b.get("status", {}).get("state") == "Completed"
                     and b.get("status", {}).get("backupCreatedAt")]
        at = max(completed) if completed else None
    return at, labels


def is_confirmed_protected(name, group_intervals, log=None):
    """True only when the volume's OWN recurring-job-group label(s) match a
    known backup-task RecurringJob AND its most recent backup is within 1.5x
    that job's cadence. Any failure or ambiguity -> False (not confirmed --
    the chunk naming this volume is left untouched, never suppressed)."""
    try:
        at, labels = _last_backup_at(name)
        groups = [k.rsplit("/", 1)[1] for k, v in labels.items()
                  if k.startswith("recurring-job-group.longhorn.io/") and v == "enabled"]
        intervals = [group_intervals[g] for g in groups if g in group_intervals]
        if not at or not intervals:
            if log:
                log("longhorn-verify: %s not confirmed (lastBackupAt=%r, "
                    "backup-task groups=%s)" % (name, at, groups))
            return False
        age_days = (datetime.datetime.now(datetime.timezone.utc)
                    - _parse_rfc3339(at)).total_seconds() / 86400.0
        threshold = 1.5 * min(intervals)
        confirmed = age_days <= threshold
        if log:
            log("longhorn-verify: %s lastBackupAt=%s age=%.1fd threshold=%.1fd "
                "groups=%s -> %s" % (name, at, age_days, threshold, groups,
                                      "CONFIRMED" if confirmed else "not confirmed"))
        return confirmed
    except Exception as e:  # noqa: BLE001 -- any API/parse failure: don't confirm
        if log:
            log("longhorn-verify: %s verify failed (%s) -- not confirmed, "
                "passing through unchanged" % (name, e))
        return False


_CHUNK_START = re.compile(r"^[ \t]*(?:[-*]\s|\d+[.)]\s|##?\s)")


def _split_chunks(body):
    """Split into finding-sized chunks: blank-line paragraphs, further split
    on any line that opens a new bullet/numbered/heading item. Same line-
    anchored-regex discipline as strip_roster()/is_empty_finding() -- no
    markdown parser."""
    chunks, cur = [], []
    for line in body.split("\n"):
        if not line.strip():
            if cur:
                chunks.append("\n".join(cur)); cur = []
            continue
        if _CHUNK_START.match(line) and cur:
            chunks.append("\n".join(cur)); cur = []
        cur.append(line)
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def suppress_confirmed(body, log=None):
    """Drop any chunk that names EXACTLY ONE known Longhorn volume and that
    volume is positively confirmed protected. Everything else -- chunks
    with zero, two+, or an unconfirmed volume name -- is returned byte-for-
    byte. On ANY exception (API unreachable, RBAC denied, malformed
    response) the body is returned UNCHANGED."""
    try:
        names = _volume_names()
        if not names:
            return body
        mentioned = {n for n in names if re.search(r"\b%s\b" % re.escape(n), body)}
        if not mentioned:
            return body
        group_intervals = _backup_task_group_intervals()
        confirmed = {n for n in mentioned
                     if is_confirmed_protected(n, group_intervals, log=log)}
        if not confirmed:
            return body
        chunks = _split_chunks(body)
        kept, dropped = [], []
        for chunk in chunks:
            hit = [n for n in mentioned if re.search(r"\b%s\b" % re.escape(n), chunk)]
            if len(hit) == 1 and hit[0] in confirmed:
                dropped.append(hit[0])
                continue
            kept.append(chunk)
        if not dropped:
            return body
        if log:
            log("longhorn-verify: dropped %d confirmed-protected chunk(s) (%s)"
                % (len(dropped), ", ".join(dropped)))
        return "\n\n".join(kept).rstrip()
    except Exception as e:  # noqa: BLE001
        if log:
            log("longhorn-verify: suppress_confirmed failed (%s) -- passing "
                "body through unchanged" % e)
        return body
