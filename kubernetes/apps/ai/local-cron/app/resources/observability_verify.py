#!/usr/bin/env python3
"""Deterministic ground-truth backstop for the observability-triage watcher.

Opt-in (OBS_VERIFY=true, set only on cronjob-observability-triage.yaml):
before the shared triage_run.py send-gate emails a PROBLEM body, two classes of
claim the local model has gotten wrong are checked against Prometheus.

1. FLUX DEPENDENCY CASCADE. The 2026-11-21 12:02 run reported
   "FluxReconciliationFailure (critical) -- ~170 Kustomizations ... pending,
   Flux unable to reconcile. Systemic." Live truth: one Kustomization
   (rook-ceph-cluster) was Progressing through a long HelmRelease upgrade and
   every other not-ready Kustomization was `DependencyNotReady` -- a DAG wait
   behind that one root, not a failure (the FluxReconciliationFailure alert
   was only `pending`). A chunk that claims Kustomizations are failing /
   pending / not ready is REWRITTEN to one line naming the real root(s), when
   flux_resource_info shows at least one DependencyNotReady Kustomization.

2. NODE FREE-SPACE FIGURES. The same run stated "spark: 70 GB free" -- spark had
   702 GB free; 70 GB was its 2-day USAGE delta (CephNodeDiskspaceWarning is a
   predict_linear "full within 5 days" rule, not a low-space threshold). Every
   "<node>: <N> GB free" figure is checked against node_filesystem_avail_bytes
   for `/`; a figure off by more than 15% is replaced in place with the live
   value.

FAIL-SAFE DIRECTION: PROM_URL unset, Prometheus unreachable, an empty or
unparseable result, or an unmatched node name -> that chunk/figure is left
UNCHANGED. This module rewrites only what it has positively measured; it never
drops a finding outright.

Stdlib only -- no deps, matches the rest of this app.
"""
import json
import os
import re
import urllib.parse
import urllib.request

import longhorn_verify  # reuse its markdown chunk splitter (same discipline)

# --- 1. Flux cascade --------------------------------------------------------
_FLUX_SUBJECT = re.compile(r"(?i)(kustomizations?\b|FluxReconciliationFailure|\bflux\b)")
_FLUX_CLAIM = re.compile(
    r"(?i)(pending|not[ \t-]*ready|fail|unable to reconcile|stuck|systemic)"
)
Q_FLUX = (
    'max by (exported_namespace, name, reason) '
    '(flux_resource_info{kind="Kustomization",ready!="True",suspended="False"})'
)

# --- 2. Free-space figures ---------------------------------------------------
# `worker8`: 132 GB free  |  spark : 70 GB free (ext4, /)  |  **worker8** 132GB free
_FREE_FIG = re.compile(
    r"(?P<node>[A-Za-z][\w.-]*)(?P<sep>[`*]*[ \t]*:?[ \t]*)"
    r"(?P<num>\d+(?:\.\d+)?)(?P<unit>[ \t]*GB)(?P<tail>[ \t]+free)"
)
Q_FREE = 'node_filesystem_avail_bytes{mountpoint="/",fstype!~"tmpfs|overlay"}'
FREE_TOLERANCE = 0.15


def _query(prom_url, expr, timeout=10):
    """Instant query -> list of (metric-labels, float) (empty list = empty vector)."""
    url = prom_url + "/api/v1/query?query=" + urllib.parse.quote(expr)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    if data.get("status") != "success":
        raise ValueError("prometheus status=%r" % data.get("status"))
    return [(s["metric"], float(s["value"][1])) for s in data["data"]["result"]]


def is_flux_cascade_claim(chunk):
    return bool(_FLUX_SUBJECT.search(chunk) and _FLUX_CLAIM.search(chunk))


def flux_cascade_line(rows):
    """Build the replacement line from flux_resource_info rows, or None when the
    live state is NOT a dependency cascade (no DependencyNotReady waiters)."""
    waiting = [m for m, _ in rows if m.get("reason") == "DependencyNotReady"]
    roots = sorted(
        "%s/%s (%s)" % (m.get("exported_namespace", "?"), m.get("name", "?"),
                        m.get("reason", "?"))
        for m, _ in rows if m.get("reason") != "DependencyNotReady"
    )
    if not waiting:
        return None
    if roots:
        return ("PROBLEM: Flux -- %d Kustomization(s) are waiting on dependencies "
                "(a DAG cascade, not failures); the root(s) not ready: %s. Next "
                "step: fix the root; the waiters reconcile on their own."
                % (len(waiting), ", ".join(roots[:5])))
    return ("PROBLEM: Flux -- %d Kustomization(s) report DependencyNotReady with "
            "no non-dependency root not ready (a reconcile wave mid-walk). Next "
            "step: re-check in 15m before acting." % len(waiting))


def rewrite_flux_cascade(body, log=None, query=None):
    chunks = longhorn_verify._split_chunks(body)
    claims = [c for c in chunks if is_flux_cascade_claim(c)]
    if not claims:
        return body
    rows = query(Q_FLUX)
    line = flux_cascade_line(rows)
    if line is None:
        if log:
            log("obs-verify: flux claim kept -- no DependencyNotReady waiters live")
        return body
    out, emitted = [], False
    for c in chunks:
        if is_flux_cascade_claim(c):
            if not emitted:
                out.append(line)
                emitted = True
            continue
        out.append(c)
    if log:
        log("obs-verify: rewrote %d flux claim chunk(s) -> %s" % (len(claims), line))
    return "\n\n".join(out).rstrip()


def _match_node(token, free_by_node):
    """Map a model-written node token (short or FQDN) to exactly one live node."""
    t = token.lower().strip("`*")
    hits = [n for n in free_by_node if n.lower() == t or n.lower().split(".")[0] == t]
    return hits[0] if len(hits) == 1 else None


def correct_free_figures(body, log=None, query=None):
    if not _FREE_FIG.search(body):
        return body
    free_by_node = {}
    for m, v in query(Q_FREE):
        node = m.get("nodename") or m.get("instance", "").split(":")[0]
        if node:
            free_by_node[node] = v / 1e9
    if not free_by_node:
        return body

    def fix(mo):
        node = _match_node(mo.group("node"), free_by_node)
        if node is None:
            return mo.group(0)
        stated, actual = float(mo.group("num")), free_by_node[node]
        if actual > 0 and abs(stated - actual) / actual <= FREE_TOLERANCE:
            return mo.group(0)
        if log:
            log("obs-verify: %s free-space figure %.0f GB -> live %.0f GB"
                % (node, stated, actual))
        return "%s%s%.0f%s%s" % (mo.group("node"), mo.group("sep"), actual,
                                 mo.group("unit"), mo.group("tail"))

    return _FREE_FIG.sub(fix, body)


def verify(body, log=None, query=None):
    """Apply both backstops. On ANY failure the body is returned UNCHANGED."""
    try:
        prom_url = os.environ.get("PROM_URL", "").rstrip("/")
        if not prom_url and query is None:
            return body
        q = query or (lambda expr: _query(prom_url, expr))
        body = rewrite_flux_cascade(body, log=log, query=q)
        return correct_free_figures(body, log=log, query=q)
    except Exception as e:  # noqa: BLE001 -- a verify failure must not bury a finding
        if log:
            log("obs-verify: failed (%s) -- passing body through unchanged" % e)
        return body
