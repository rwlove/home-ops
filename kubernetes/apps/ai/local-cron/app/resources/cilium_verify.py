#!/usr/bin/env python3
"""Deterministic flow-table-exhaustion backstop for the network-triage watcher.

Opt-in (CILIUM_VERIFY_FLOWS=true, set only on cronjob-network-triage.yaml):
before the shared triage_run.py send-gate emails a PROBLEM body, any chunk that
CLAIMS Cilium/Hubble flow-table, conntrack, or BPF-map exhaustion is checked
against Prometheus ground truth. If every signal positively confirms the maps
are healthy, that chunk -- and ONLY that chunk -- is dropped.

Why: the network-operator (local 35B) has twice paged "flow table exhaustion on
all nodes, connections dropped" (2026-11-15, 2026-11-21) off `hubble status` /
`hubble list nodes` reading "Current/Max Flows 65,535/65,535 (100%)". That
figure is the Hubble observer RING BUFFER (cilium values.yaml
`hubble.eventBufferCapacity: "65535"`) -- it reads 100% by design once it wraps
and never drops traffic. The 11-21 page also proposed raising
`bpf-map-dynamic-size-ratio` from 0.0025 "100x below the default of 0.25" --
0.0025 IS Cilium's default; the suggested change would have bloated every BPF
map 100x behind a cluster-wide agent restart. The prompt-level grounding guard
(home-ops#15951) did not hold, so per the fleet rule (model-reasoning false
positive -> fix deterministically in the harness, not by prompt-steering) the
claim is checked against the real signals here.

Ground truth (all must confirm; any one failing keeps the chunk):
  1. cilium_bpf_map_pressure for CT/NAT maps returns data AND its max < 0.9
  2. hubble_lost_events_total increase over the window returns a value == 0
  3. cilium_drop_count_total for map-insertion-failure reasons over the window
     is 0 (an empty vector counts as 0 -- only once (1) has proven Prometheus
     answers and the Cilium metrics exist)

FAIL-SAFE DIRECTION: this module can only ever REMOVE a chunk it has POSITIVELY
verified as a false claim. PROM_URL unset, Prometheus unreachable, an empty or
unparseable result, or real pressure/drops -> the body is returned UNCHANGED.
A verify failure must never bury a real finding.

Stdlib only -- no deps, matches the rest of this app.
"""
import json
import os
import re
import urllib.parse
import urllib.request

import longhorn_verify  # reuse its markdown chunk splitter (same discipline)

PRESSURE_MAX = 0.9
WINDOW = "6h"  # matches the network-triage cadence

# A chunk is a flow-exhaustion CLAIM only when it names a flow/conntrack/BPF-map
# subject AND an exhaustion verb/figure. Both halves required so an unrelated
# finding that merely mentions Hubble (e.g. a policy-drop verdict) is untouched.
_SUBJECT = re.compile(
    r"(?i)(flow[ \t-]*(table|map|buffer|capacity)|\bflows?\b.{0,40}\bhubble\b|"
    r"\bhubble\b.{0,60}\bflows?\b|conntrack|\bct[ \t_-]*(map|table)|"
    r"bpf[ \t_-]*map|bpf-map-dynamic-size-ratio|flow-table-size|"
    r"65,?535[ \t]*/[ \t]*65,?535)"
)
_EXHAUST = re.compile(
    r"(?i)(exhaust|saturat|\bfull\b|100[ \t]*%|at capacity|overflow|"
    r"being dropped|connections? (are |is )?(being )?dropped|dropping)"
)

Q_PRESSURE = 'max(cilium_bpf_map_pressure{map_name=~"(ct|nat|snat).*"})'
Q_LOST = "sum(increase(hubble_lost_events_total[%s]))" % WINDOW
Q_INSERT_DROPS = (
    'sum(increase(cilium_drop_count_total{reason=~"(?i).*(insertion failed|'
    'map full|no space).*"}[%s]))' % WINDOW
)


def is_flow_exhaustion_claim(chunk):
    return bool(_SUBJECT.search(chunk) and _EXHAUST.search(chunk))


def _query(prom_url, expr, timeout=10):
    """Instant query -> list of float sample values (empty list = empty vector)."""
    url = prom_url + "/api/v1/query?query=" + urllib.parse.quote(expr)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    if data.get("status") != "success":
        raise ValueError("prometheus status=%r" % data.get("status"))
    return [float(s["value"][1]) for s in data["data"]["result"]]


def maps_confirmed_healthy(prom_url, log=None, query=None):
    """True only when all three ground-truth signals positively confirm no
    flow/conntrack exhaustion. Any failure or ambiguity -> False."""
    q = query or (lambda expr: _query(prom_url, expr))
    try:
        pressure = q(Q_PRESSURE)
        lost = q(Q_LOST)
        drops = q(Q_INSERT_DROPS)
    except Exception as e:  # noqa: BLE001 -- any fetch/parse failure: don't confirm
        if log:
            log("cilium-verify: prometheus query failed (%s) -- not confirmed" % e)
        return False
    ok = (bool(pressure) and max(pressure) < PRESSURE_MAX
          and bool(lost) and sum(lost) == 0
          and sum(drops) == 0)
    if log:
        log("cilium-verify: ct/nat map pressure max=%s lost_events(%s)=%s "
            "insertion_drops(%s)=%s -> %s"
            % (pressure and "%.3f" % max(pressure), WINDOW, lost and sum(lost),
               WINDOW, sum(drops), "HEALTHY" if ok else "not confirmed"))
    return ok


def suppress_false_exhaustion(body, log=None, query=None):
    """Drop every chunk that claims flow/conntrack/BPF-map exhaustion when
    Prometheus positively confirms the maps are healthy. Everything else is
    returned unchanged; on ANY failure the body is returned UNCHANGED."""
    try:
        prom_url = os.environ.get("PROM_URL", "").rstrip("/")
        if not prom_url and query is None:
            return body
        chunks = longhorn_verify._split_chunks(body)
        claims = [c for c in chunks if is_flow_exhaustion_claim(c)]
        if not claims:
            return body
        if not maps_confirmed_healthy(prom_url, log=log, query=query):
            return body
        kept = [c for c in chunks if not is_flow_exhaustion_claim(c)]
        if log:
            log("cilium-verify: dropped %d flow-exhaustion claim chunk(s) "
                "contradicted by live map pressure / lost-event / drop metrics"
                % len(claims))
        return "\n\n".join(kept).rstrip()
    except Exception as e:  # noqa: BLE001
        if log:
            log("cilium-verify: suppress failed (%s) -- passing body through "
                "unchanged" % e)
        return body
