#!/usr/bin/env python3
"""Loop A — event-driven NetBox write-back (Phase 3), the programmatic complement
to the reconcile-on-write discipline doc.

When the net operator mutates LIVE state — an Omada OpenAPI port/client change, a
DNS/DHCP record via the netop brain foothold, or a direct NetBox edit — the
operator calls this helper in the SAME change to mirror the mutation into NetBox
immediately, while intent is known. That is strictly better than waiting for the
nightly Loop B sweep to infer it. NetBox stays a mirror; we never push NetBox →
devices.

This module is **import-safe and side-effect free on import**, and every write is
**opt-in** (`apply=True`). The CLI defaults to DRY-RUN, so merging this code
changes nothing and writes nothing until the operator explicitly runs it with
`--apply`. There is NO unattended workload wired to it here (that is Phase 4, and
ships OFF).

Records this tool creates/updates are tagged `netbox-sync` (OWNED_TAG) so Phase 4
can scope a write-token / auto-write to only the objects the sync owns.

Credentials: NETBOX_URL + NETBOX_TOKEN from the environment — the operator's own
token at call time. This module never bakes or logs a token. For Loop A the token
only needs write on ipam ip-addresses/prefixes/vlans (the models below).

Stdlib only.
"""
import json
import os
import urllib.error
import urllib.request

OWNED_TAG = os.environ.get("NETBOX_OWNED_TAG", "netbox-sync")


class WritebackError(RuntimeError):
    pass


class NetBox:
    def __init__(self, base_url=None, token=None, timeout=30):
        self.base = (base_url or os.environ.get("NETBOX_URL", "")).rstrip("/")
        self.token = token or os.environ.get("NETBOX_TOKEN", "")
        self.timeout = timeout
        if not self.base:
            raise WritebackError("NETBOX_URL is required")

    def _req(self, method, path, body=None):
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = "Token %s" % self.token
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8", "replace")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            raise WritebackError("%s %s -> HTTP %s %s" % (method, path, e.code, detail))
        except Exception as e:  # noqa: BLE001
            raise WritebackError("%s %s -> %s" % (method, path, e))

    # -- generic helpers --------------------------------------------------
    def get_first(self, path, query):
        qs = "&".join("%s=%s" % (k, urllib.request.quote(str(v), safe=""))
                      for k, v in query.items())
        res = self._req("GET", "%s?%s" % (path, qs))
        results = res.get("results") or []
        return results[0] if results else None

    def _tags(self, use_tag):
        return [{"slug": OWNED_TAG}] if use_tag else None


def _merge(desired, use_tag, nb):
    payload = {k: v for k, v in desired.items() if v is not None}
    if use_tag:
        payload["tags"] = nb._tags(True)
    return payload


def _changed(existing, desired):
    """Return the subset of `desired` whose value differs from `existing`."""
    diff = {}
    for k, v in desired.items():
        if v is None:
            continue
        cur = existing.get(k)
        if k == "vlan" and isinstance(cur, dict):
            cur = cur.get("id")
        if str(cur or "") != str(v):
            diff[k] = v
    return diff


# ---------------------------------------------------------------------------
# IP address (the primary Loop A object — DNS/DHCP reservation reconciliation)
# ---------------------------------------------------------------------------
def upsert_ip(nb, address, dns_name=None, description=None, apply=False, use_tag=True):
    existing = nb.get_first("/api/ipam/ip-addresses/", {"address": address})
    desired = {"dns_name": dns_name, "description": description}
    if existing is None:
        action = "create ip-address %s%s" % (address,
                  " dns_name=%s" % dns_name if dns_name else "")
        if apply:
            body = {"address": address}
            body.update(_merge(desired, use_tag, nb))
            nb._req("POST", "/api/ipam/ip-addresses/", body)
        return action
    diff = _changed(existing, desired)
    if not diff:
        return "no-op ip-address %s (already matches)" % address
    action = "update ip-address %s: %s" % (address, diff)
    if apply:
        if use_tag:
            diff["tags"] = nb._tags(True)
        nb._req("PATCH", "/api/ipam/ip-addresses/%d/" % existing["id"], diff)
    return action


def delete_ip(nb, address, apply=False):
    existing = nb.get_first("/api/ipam/ip-addresses/", {"address": address})
    if existing is None:
        return "no-op delete ip-address %s (absent)" % address
    action = "delete ip-address %s (id=%d)" % (address, existing["id"])
    if apply:
        nb._req("DELETE", "/api/ipam/ip-addresses/%d/" % existing["id"])
    return action


# ---------------------------------------------------------------------------
# Prefix
# ---------------------------------------------------------------------------
def upsert_prefix(nb, prefix, description=None, apply=False, use_tag=True):
    existing = nb.get_first("/api/ipam/prefixes/", {"prefix": prefix})
    desired = {"description": description}
    if existing is None:
        action = "create prefix %s" % prefix
        if apply:
            body = {"prefix": prefix}
            body.update(_merge(desired, use_tag, nb))
            nb._req("POST", "/api/ipam/prefixes/", body)
        return action
    diff = _changed(existing, desired)
    if not diff:
        return "no-op prefix %s (already matches)" % prefix
    action = "update prefix %s: %s" % (prefix, diff)
    if apply:
        if use_tag:
            diff["tags"] = nb._tags(True)
        nb._req("PATCH", "/api/ipam/prefixes/%d/" % existing["id"], diff)
    return action


# ---------------------------------------------------------------------------
# VLAN
# ---------------------------------------------------------------------------
def upsert_vlan(nb, vid, name, apply=False, use_tag=True):
    existing = nb.get_first("/api/ipam/vlans/", {"vid": vid})
    desired = {"name": name}
    if existing is None:
        action = "create vlan vid=%s name=%s" % (vid, name)
        if apply:
            body = {"vid": int(vid), "name": name}
            if use_tag:
                body["tags"] = nb._tags(True)
            nb._req("POST", "/api/ipam/vlans/", body)
        return action
    diff = _changed(existing, desired)
    if not diff:
        return "no-op vlan vid=%s (already matches)" % vid
    action = "update vlan vid=%s: %s" % (vid, diff)
    if apply:
        if use_tag:
            diff["tags"] = nb._tags(True)
        nb._req("PATCH", "/api/ipam/vlans/%d/" % existing["id"], diff)
    return action


def verify_ip(nb, address):
    """Read-back verification after an apply."""
    return nb.get_first("/api/ipam/ip-addresses/", {"address": address})
