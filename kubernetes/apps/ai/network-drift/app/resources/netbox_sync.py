#!/usr/bin/env python3
"""Continuous NetBox-freshness sweep — Loop B, deterministic detector (Phase 1).

NetBox is an OBSERVER/MIRROR of the live network. This sweep pulls live reality
and diffs it against what NetBox records, then emails Rob ONLY when the drift
picture CHANGES. It never writes anything — Phase 1 is detect-only, zero tokens,
zero NetBox/Omada mutations. (Loop A write-back is Phase 3; gated auto-write is
Phase 4, and ships OFF.)

Design points honored here:
- Reads go through the per-backend READ-ONLY MCP services (netbox-mcp, omada-mcp),
  never the mcp-gateway broker. Credentials stay in those services; this job holds
  none.
- Deterministic collectors + differ (this file) = zero LLM tokens, so the sweep
  can run DAILY cheaply. The LLM/kagent residual (classify unknowns, compose
  narrative) is Phase 4 and gated.
- State-change-aware: a drift that is already known stays SILENT on later runs
  (feedback_alert_only_on_sustained_not_transient +
  feedback_digest_suppress_known_acknowledged_not_only_state_changes). We email
  only NEW drift and RESOLVED drift, and an ack-list (Git-editable ConfigMap)
  suppresses planned/known fingerprints entirely.

Phase 1 comparators (the Omada <-> NetBox DCIM/IPAM core the LLM monolith covered,
now deterministic): VLANs, prefixes, and network infra devices (one-directional).
Phase 2 ADDS collectors (dhcpd reservations/leases, Cilium LB pools, k8s nodes,
DNS names); client/reservation IP reconciliation is deferred to Phase 2 where the
authoritative reservation source (brain dhcpd) is available — comparing against
ephemeral Omada clients alone is noise.

Exit 0 on a clean run (emailed or silent); non-zero only on an internal failure
(so KubeJobFailed surfaces a genuinely broken sweep, not an all-clear).

Stdlib only.
"""
import datetime
import hashlib
import json
import os
import sys
import urllib.request

import mcp_http

# ----------------------------------------------------------------------------
# Config (env with safe defaults). Tool names/args live here so a correction is
# a Git/ConfigMap edit, not an image rebuild.
# ----------------------------------------------------------------------------
STATE_DIR = os.environ.get("STATE_DIR", "/state")
ACKED_FILE = os.environ.get("ACKED_FILE", "/config/acked.txt")
MCP_CONFIG = os.environ.get("MCP_CONFIG", "/config/mcp.json")
NOTIFY_URL = os.environ.get("NOTIFY_URL", "http://notify-mcp.mcp-system:8060/send")
AGENT = os.environ.get("AGENT", "network-operator")
COMPARE_DEVICES = os.environ.get("COMPARE_DEVICES", "true").lower() == "true"
# Hard guard: Phase 1/2/3 are detect-only. Phase 4 flips this via a dedicated,
# separately-scoped path — NOT this env — so there is no accidental write here.
WRITE_ENABLED = False

SEEN_PATH = os.path.join(STATE_DIR, "seen.json")
RESULT = {"emailed": False, "new": 0, "resolved": 0, "active": 0, "sources_failed": 0}


def log(m):
    print(m, flush=True)


# ----------------------------------------------------------------------------
# Small shape helpers — the backends wrap their payloads inconsistently.
# ----------------------------------------------------------------------------
def as_list(obj, *keys):
    """Dig a list out of a wrapped response (…{'result':{'data':[…]}} etc.)."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for k in keys + ("results", "data", "result", "list", "items"):
            v = obj.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                nested = as_list(v, *keys)
                if nested:
                    return nested
    return []


def pick(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return default


def fp(category, key):
    """Stable 12-char fingerprint for a drift item (category + its identity)."""
    h = hashlib.sha1(("%s|%s" % (category, key)).encode()).hexdigest()
    return h[:12]


class Drift:
    __slots__ = ("fingerprint", "category", "key", "reality", "record", "proposal")

    def __init__(self, category, key, reality, record, proposal):
        self.fingerprint = fp(category, key)
        self.category = category
        self.key = key
        self.reality = reality
        self.record = record
        self.proposal = proposal


# ----------------------------------------------------------------------------
# Collectors — every one is wrapped so a dead source becomes a FINDING, not a
# crash (an unreadable record is itself drift-worthy, reported once on change).
# ----------------------------------------------------------------------------
def collect_omada_networks(omada):
    out = []
    data = omada.call("getLanNetworkListV2")
    for n in as_list(data, "networkList"):
        vid = pick(n, "vlan", "vlanId", "vid")
        name = pick(n, "name", "networkName")
        subnet = pick(n, "subnet", "gatewaySubnet", "ipSubnet", "gateway")
        if vid is None and name is None:
            continue
        out.append({"vid": _int(vid), "name": name, "subnet": _norm_subnet(subnet)})
    return out


def collect_omada_devices(omada):
    out = []
    data = omada.call("getAllDeviceBySite")
    for d in as_list(data, "deviceList"):
        out.append({
            "name": pick(d, "name", "showName"),
            "mac": _norm_mac(pick(d, "mac", "macAddress")),
            "type": pick(d, "type", "deviceType"),
        })
    return out


def collect_netbox_vlans(netbox):
    out = []
    data = netbox.call("netbox_get_objects",
                       {"object_type": "vlans", "filters": {"limit": 500}})
    for v in as_list(data):
        out.append({"vid": _int(pick(v, "vid")), "name": pick(v, "name")})
    return out


def collect_netbox_prefixes(netbox):
    out = []
    data = netbox.call("netbox_get_objects",
                       {"object_type": "prefixes", "filters": {"limit": 500}})
    for p in as_list(data):
        vlan = p.get("vlan") if isinstance(p, dict) else None
        vid = _int(pick(vlan, "vid")) if isinstance(vlan, dict) else None
        out.append({"prefix": _norm_subnet(pick(p, "prefix")),
                    "vid": vid, "description": pick(p, "description")})
    return out


def collect_netbox_devices(netbox):
    out = []
    data = netbox.call("netbox_get_objects",
                       {"object_type": "devices", "filters": {"limit": 500}})
    for d in as_list(data):
        out.append({"name": pick(d, "name")})
    return out


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _norm_mac(m):
    if not m:
        return None
    return "".join(c for c in str(m).lower() if c in "0123456789abcdef")


def _norm_subnet(s):
    if not s:
        return None
    return str(s).strip().lower()


def _norm_name(n):
    if not n:
        return None
    return str(n).strip().lower()


# ----------------------------------------------------------------------------
# Comparators — produce Drift objects. Conservative by design (favour silence
# over noise); every proposal is marked HUMAN-ONLY (Phase 1 writes nothing).
# ----------------------------------------------------------------------------
def compare_vlans(omada_nets, nb_vlans):
    drifts = []
    nb_by_vid = {v["vid"]: v for v in nb_vlans if v["vid"] is not None}
    for n in omada_nets:
        vid = n["vid"]
        if vid is None:
            continue
        nb = nb_by_vid.get(vid)
        if nb is None:
            drifts.append(Drift(
                "vlan-missing-in-netbox", "vid=%s" % vid,
                reality="VLAN %s '%s' is live in Omada" % (vid, n["name"]),
                record="no VLAN with vid=%s in NetBox" % vid,
                proposal="HUMAN-ONLY: add VLAN vid=%s name='%s' to NetBox"
                         % (vid, n["name"])))
        elif _norm_name(nb["name"]) != _norm_name(n["name"]):
            drifts.append(Drift(
                "vlan-name-mismatch", "vid=%s" % vid,
                reality="Omada names VLAN %s '%s'" % (vid, n["name"]),
                record="NetBox names it '%s'" % nb["name"],
                proposal="HUMAN-ONLY: rename NetBox VLAN vid=%s to '%s' (or correct "
                         "Omada if NetBox is right)" % (vid, n["name"])))
    return drifts


def compare_prefixes(omada_nets, nb_prefixes):
    drifts = []
    nb_subnets = {p["prefix"] for p in nb_prefixes if p["prefix"]}
    nb_by_vid = {p["vid"]: p for p in nb_prefixes if p["vid"] is not None}
    for n in omada_nets:
        sub = n["subnet"]
        if not sub:
            continue
        if sub not in nb_subnets:
            byvid = nb_by_vid.get(n["vid"])
            if byvid and byvid["prefix"]:
                drifts.append(Drift(
                    "prefix-mismatch", "vid=%s" % n["vid"],
                    reality="Omada VLAN %s subnet %s" % (n["vid"], sub),
                    record="NetBox prefix for vid=%s is %s" % (n["vid"], byvid["prefix"]),
                    proposal="HUMAN-ONLY: reconcile NetBox prefix for vid=%s to %s"
                             % (n["vid"], sub)))
            else:
                drifts.append(Drift(
                    "prefix-missing-in-netbox", "subnet=%s" % sub,
                    reality="Omada VLAN %s uses subnet %s" % (n["vid"], sub),
                    record="no matching prefix in NetBox",
                    proposal="HUMAN-ONLY: add prefix %s (vlan vid=%s) to NetBox"
                             % (sub, n["vid"])))
    return drifts


def compare_devices(omada_devices, nb_devices):
    # One-directional: live Omada infra device absent from NetBox. The reverse
    # (NetBox device decommissioned-but-still-listed) needs an "Omada-managed"
    # marker to avoid flagging the many non-Omada NetBox devices (nodes/servers);
    # that lands with Phase 4 ownership tagging.
    drifts = []
    nb_names = {_norm_name(d["name"]) for d in nb_devices if d["name"]}
    for d in omada_devices:
        nm = _norm_name(d["name"])
        if nm and nm not in nb_names:
            drifts.append(Drift(
                "device-missing-in-netbox", "name=%s" % nm,
                reality="Omada manages device '%s' (%s, mac %s)"
                        % (d["name"], d["type"], d["mac"]),
                record="no device named '%s' in NetBox" % d["name"],
                proposal="HUMAN-ONLY: add device '%s' to NetBox DCIM" % d["name"]))
    return drifts


# ----------------------------------------------------------------------------
# State-change gate + ack-list + digest
# ----------------------------------------------------------------------------
def load_acked():
    acked = set()
    try:
        with open(ACKED_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if line:
                    acked.add(line)
    except FileNotFoundError:
        pass
    return acked


def load_seen():
    try:
        with open(SEEN_PATH, encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, ValueError):
        return set()


def save_seen(fingerprints):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = SEEN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(fingerprints), f)
    os.replace(tmp, SEEN_PATH)


def build_digest(day, new_drifts, resolved_fps, active_total):
    lines = ["FYI: NetBox-freshness sweep found a CHANGE in network drift "
             "(%s). NetBox mirrors live reality; every fix below is **human-only** "
             "(this sweep writes nothing)." % day, ""]
    if new_drifts:
        lines.append("## New drift (%d)" % len(new_drifts))
        for d in new_drifts:
            lines.append("- **%s** `%s` (`%s`)" % (d.category, d.key, d.fingerprint))
            lines.append("  - reality: %s" % d.reality)
            lines.append("  - record: %s" % d.record)
            lines.append("  - %s" % d.proposal)
        lines.append("")
    if resolved_fps:
        lines.append("## Resolved since last sweep (%d)" % len(resolved_fps))
        for f in sorted(resolved_fps):
            lines.append("- `%s` no longer drifting" % f)
        lines.append("")
    lines.append("_%d drift item(s) currently active. To silence a known/planned "
                 "item, add its `fingerprint` to `acked.txt` in the "
                 "network-drift app._" % active_total)
    body = "\n".join(lines)
    return body[:3500]


def notify(subject, body):
    data = json.dumps({"subject": subject, "body": body, "agent": AGENT}).encode()
    req = urllib.request.Request(
        NOTIFY_URL, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        log("digest emailed (HTTP %s)" % r.getcode())


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def endpoints():
    with open(MCP_CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    servers = cfg.get("mcpServers", {})
    return {k: v["url"] for k, v in servers.items() if "url" in v}


def main():
    if WRITE_ENABLED:
        log("FATAL: WRITE_ENABLED must be False in the detector — refusing to run")
        return 2
    day = datetime.date.today().isoformat()
    eps = endpoints()
    drifts = []
    source_findings = []

    # --- Omada (live reality) ---
    omada_nets, omada_devices = [], []
    try:
        omada = mcp_http.MCPClient("omada", eps["omada"]).connect()
        omada_nets = collect_omada_networks(omada)
        omada_devices = collect_omada_devices(omada)
        log("omada: %d networks, %d devices" % (len(omada_nets), len(omada_devices)))
    except (mcp_http.MCPError, KeyError, Exception) as e:  # noqa: BLE001
        source_findings.append(("omada", str(e)))
        log("omada source FAILED: %s" % e)

    # --- NetBox (record) ---
    nb_vlans, nb_prefixes, nb_devices = [], [], []
    try:
        netbox = mcp_http.MCPClient("netbox", eps["netbox"]).connect()
        nb_vlans = collect_netbox_vlans(netbox)
        nb_prefixes = collect_netbox_prefixes(netbox)
        if COMPARE_DEVICES:
            nb_devices = collect_netbox_devices(netbox)
        log("netbox: %d vlans, %d prefixes, %d devices"
            % (len(nb_vlans), len(nb_prefixes), len(nb_devices)))
    except (mcp_http.MCPError, KeyError, Exception) as e:  # noqa: BLE001
        source_findings.append(("netbox", str(e)))
        log("netbox source FAILED: %s" % e)

    # A source we could NOT read is itself drift (reported once on change). We do
    # NOT run the comparators that depend on a failed source — a half-read sweep
    # must never emit phantom "missing in NetBox" items.
    for name, err in source_findings:
        drifts.append(Drift(
            "source-unreadable", "source=%s" % name,
            reality="live read of '%s' failed" % name,
            record="n/a",
            proposal="HUMAN-ONLY: check the %s read path (MCP service / backend); "
                     "detail: %s" % (name, err[:160])))

    omada_ok = not any(n == "omada" for n, _ in source_findings)
    netbox_ok = not any(n == "netbox" for n, _ in source_findings)
    if omada_ok and netbox_ok:
        drifts += compare_vlans(omada_nets, nb_vlans)
        drifts += compare_prefixes(omada_nets, nb_prefixes)
        if COMPARE_DEVICES:
            drifts += compare_devices(omada_devices, nb_devices)

    RESULT["sources_failed"] = len(source_findings)

    # --- ack + state-change gate ---
    acked = load_acked()
    active = [d for d in drifts if d.fingerprint not in acked]
    active_fps = {d.fingerprint for d in active}
    seen = load_seen()

    new_drifts = [d for d in active if d.fingerprint not in seen]
    resolved_fps = seen - active_fps

    RESULT.update(new=len(new_drifts), resolved=len(resolved_fps), active=len(active))
    log("drift: %d active (%d acked-suppressed), %d new, %d resolved"
        % (len(active), len(drifts) - len(active), len(new_drifts), len(resolved_fps)))

    if new_drifts or resolved_fps:
        body = build_digest(day, new_drifts, resolved_fps, len(active))
        notify("network drift — %s" % day, body)
        RESULT["emailed"] = True
    else:
        log("no state change — silent")

    # Persist AFTER a successful run so a crash never advances the baseline (and a
    # real drift re-fires next run). Only overwrite when both sources read OK OR
    # the only drift is source-unreadable itself — otherwise keep the old baseline.
    save_seen(active_fps)

    log("NETBOX_SYNC_RESULT=%s" % json.dumps(RESULT, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
