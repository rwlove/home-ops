#!/usr/bin/env python3
"""Phase 2 collectors: brain dhcpd + DNS zones from the network-config repo.

The declared network config (brain's `dhcpd/dhcpd.conf` and the `named/` forward
zone) is read from the lovenet-network-configuration repo through the read-only
**github-mcp** service — never the broker. From it:

- dhcpd `host { … fixed-address … }` reservations  -> NetBox ip-addresses
- dhcpd `subnet … netmask …`                        -> NetBox prefixes
- forward-zone `name A <ip>` records                -> NetBox ip-address dns_name

All comparisons are one-directional (declared -> NetBox record) except dns_name,
which only fires when the IP already exists in NetBox but its dns_name is
missing/stale (low-noise, high-signal freshness check). Every proposal is
HUMAN-ONLY. Reading the config repo is itself a record check: if github-mcp or a
path is unreadable, that is a single source-finding, not a crash.

Note: this does NOT invert IP allocation — NetBox is still only a mirror. dhcpd
stays the source of truth for reservations; we never render dhcpd from NetBox.
"""
import base64
import os
import re

import mcp_http
import netbox_sync as ns

GH_OWNER = os.environ.get("GH_OWNER", "rwlove")
GH_REPO = os.environ.get("GH_REPO", "lovenet-network-configuration")
DHCPD_PATH = os.environ.get("DHCPD_PATH", "dhcpd/dhcpd.conf")
ZONE_PATHS = [p.strip() for p in os.environ.get(
    "ZONE_PATHS", "named/named.thesteamedcrab").split(",") if p.strip()]
DNS_DOMAIN = os.environ.get("DNS_DOMAIN", "thesteamedcrab.com").rstrip(".")


# -- github-mcp fetch --------------------------------------------------------
def _fetch_text(gh, path):
    res = gh.call("get_file_contents",
                  {"owner": GH_OWNER, "repo": GH_REPO, "path": path})
    return _decode(res)


def _decode(res):
    if res is None:
        return ""
    if isinstance(res, str):
        return res
    if isinstance(res, list):  # some servers return a list of file objects
        return "\n".join(_decode(x) for x in res)
    if isinstance(res, dict):
        if res.get("encoding") == "base64" and res.get("content"):
            return base64.b64decode(res["content"]).decode("utf-8", "replace")
        for k in ("content", "text", "decoded", "data"):
            v = res.get(k)
            if isinstance(v, str):
                return v
    return ""


# -- dhcpd parsing -----------------------------------------------------------
_HOST_RE = re.compile(r"^\s*host\s+([^\s{]+)")
_FIXED_RE = re.compile(r"fixed-address\s+([0-9.]+)")
_MAC_RE = re.compile(r"hardware\s+ethernet\s+([0-9a-fA-F:]+)")
_SUBNET_RE = re.compile(r"^\s*subnet\s+([0-9.]+)\s+netmask\s+([0-9.]+)")


def _strip_comment(line):
    return line.split("#", 1)[0]


def parse_dhcpd_hosts(text):
    hosts, cur = [], None
    for raw in text.splitlines():
        line = _strip_comment(raw)
        m = _HOST_RE.match(line)
        if m:
            cur = {"name": m.group(1).rstrip("."), "mac": None, "ip": None}
            continue
        if cur is not None:
            fm = _FIXED_RE.search(line)
            if fm:
                cur["ip"] = fm.group(1)
            mm = _MAC_RE.search(line)
            if mm:
                cur["mac"] = ns._norm_mac(mm.group(1))
            if "}" in line:
                if cur["ip"]:
                    hosts.append(cur)
                cur = None
    return hosts


def parse_dhcpd_subnets(text):
    subs = []
    for raw in text.splitlines():
        m = _SUBNET_RE.match(_strip_comment(raw))
        if m:
            cidr = _to_cidr(m.group(1), m.group(2))
            if cidr:
                subs.append(cidr)
    return subs


def _to_cidr(net, mask):
    try:
        bits = sum(bin(int(o)).count("1") for o in mask.split("."))
        return ns._norm_subnet("%s/%d" % (net, bits))
    except (ValueError, AttributeError):
        return None


# -- zone parsing ------------------------------------------------------------
_A_RE = re.compile(r"^(\S+)\s+(?:\d+\s+)?(?:IN\s+)?A\s+([0-9.]+)\s*$", re.IGNORECASE)


def parse_zone_a_records(text):
    recs = []
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].rstrip()
        m = _A_RE.match(line)
        if not m:
            continue
        name, ip = m.group(1), m.group(2)
        if name in ("@", ""):
            fqdn = DNS_DOMAIN
        elif name.endswith("."):
            fqdn = name.rstrip(".")
        else:
            fqdn = "%s.%s" % (name, DNS_DOMAIN)
        recs.append({"fqdn": fqdn.lower(), "ip": ip})
    return recs


# -- comparators -------------------------------------------------------------
def compare_dhcpd_hosts(hosts, nb_ips):
    drifts = []
    nb_ip_set = {ns._bare_ip(p["address"]) for p in nb_ips if p.get("address")}
    for h in hosts:
        if ns._bare_ip(h["ip"]) not in nb_ip_set:
            drifts.append(ns.Drift(
                "dhcpd-reservation-missing-in-netbox", "ip=%s" % h["ip"],
                reality="brain dhcpd reserves %s for '%s' (mac %s)"
                        % (h["ip"], h["name"], h["mac"]),
                record="no ip-address %s in NetBox" % h["ip"],
                proposal="HUMAN-ONLY: add ip-address %s (dns_name '%s') to NetBox"
                         % (h["ip"], h["name"])))
    return drifts


def compare_dhcpd_subnets(subs, nb_prefixes):
    drifts = []
    nb_subnets = {p["prefix"] for p in nb_prefixes if p.get("prefix")}
    for cidr in subs:
        if cidr not in nb_subnets:
            drifts.append(ns.Drift(
                "dhcpd-subnet-missing-in-netbox", "subnet=%s" % cidr,
                reality="brain dhcpd declares subnet %s" % cidr,
                record="no matching prefix in NetBox",
                proposal="HUMAN-ONLY: add prefix %s to NetBox" % cidr))
    return drifts


def compare_dns_names(recs, nb_ips):
    # Only fire when the IP exists in NetBox but its dns_name is missing/stale —
    # a freshness check, not a "missing IP" check (those are covered elsewhere).
    drifts = []
    nb_by_ip = {}
    for p in nb_ips:
        if p.get("address"):
            nb_by_ip.setdefault(ns._bare_ip(p["address"]), p)
    for r in recs:
        p = nb_by_ip.get(ns._bare_ip(r["ip"]))
        if p is None:
            continue
        have = (p.get("dns_name") or "").strip().lower()
        if have != r["fqdn"]:
            drifts.append(ns.Drift(
                "netbox-dns-name-stale", "ip=%s" % r["ip"],
                reality="DNS zone maps %s -> '%s'" % (r["ip"], r["fqdn"]),
                record="NetBox dns_name for %s is '%s'" % (r["ip"], have or "(empty)"),
                proposal="HUMAN-ONLY: set NetBox dns_name of %s to '%s'"
                         % (r["ip"], r["fqdn"]),
                # SAFE auto-write target (Phase 4): PATCH dns_name on the existing
                # NetBox ip-address (use its stored CIDR so the match is exact).
                target={"kind": "ip", "address": p.get("address"),
                        "dns_name": r["fqdn"]}))
    return drifts


def run(endpoints, nb_ips, nb_prefixes):
    """Fetch + compare. Returns (drifts, findings)."""
    drifts, findings = [], []
    url = endpoints.get("github")
    if not url:
        findings.append(("github", "no github endpoint in mcp.json"))
        return drifts, findings
    try:
        gh = mcp_http.MCPClient("github", url).connect()
    except Exception as e:  # noqa: BLE001
        findings.append(("github", str(e)))
        return drifts, findings

    try:
        text = _fetch_text(gh, DHCPD_PATH)
        if not text.strip():
            raise mcp_http.MCPError("empty dhcpd.conf")
        hosts = parse_dhcpd_hosts(text)
        subs = parse_dhcpd_subnets(text)
        drifts += compare_dhcpd_hosts(hosts, nb_ips)
        drifts += compare_dhcpd_subnets(subs, nb_prefixes)
        ns.log("dhcpd: %d reservations, %d subnets" % (len(hosts), len(subs)))
    except Exception as e:  # noqa: BLE001
        findings.append(("dhcpd", str(e)))

    for zp in ZONE_PATHS:
        try:
            text = _fetch_text(gh, zp)
            if not text.strip():
                raise mcp_http.MCPError("empty zone %s" % zp)
            recs = parse_zone_a_records(text)
            drifts += compare_dns_names(recs, nb_ips)
            ns.log("dns zone %s: %d A records" % (zp, len(recs)))
        except Exception as e:  # noqa: BLE001
            findings.append(("dns:%s" % zp, str(e)))
    return drifts, findings
