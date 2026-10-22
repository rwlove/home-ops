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
        # None = tag availability unchecked; set by ensure_tag().
        self._tag_ok = None
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

    def ensure_tag(self):
        """Create-or-get the OWNED_TAG so tagged writes never 400 on a missing
        tag. Sets self._tag_ok: True = tag present & usable; False = tag is
        unavailable (this token cannot view or create it) -> callers then write
        UNTAGGED rather than failing every PATCH. NEVER raises — a tag problem
        must not block the dns_name reconciliation itself.

        Requires on the write token's user: `extras.view_tag` to attach an
        existing tag, plus `extras.add_tag` only if this should also CREATE the
        tag when absent (e.g. after a from-scratch NetBox rebuild). With neither,
        writes degrade to untagged + a logged warning rather than a hard failure.
        """
        try:
            if self.get_first("/api/extras/tags/", {"slug": OWNED_TAG}) is not None:
                self._tag_ok = True
                return "tag '%s' present" % OWNED_TAG
            try:
                self._req("POST", "/api/extras/tags/",
                          {"name": OWNED_TAG, "slug": OWNED_TAG, "color": "00bcd4",
                           "description": "Applied by network-drift Phase 4 auto-write"})
                self._tag_ok = True
                return "tag '%s' created" % OWNED_TAG
            except WritebackError as e:
                self._tag_ok = False
                return ("tag '%s' absent and not creatable by this token (%s) -> "
                        "writing UNTAGGED" % (OWNED_TAG, e))
        except WritebackError as e:
            self._tag_ok = False
            return ("tag '%s' not viewable by this token (%s) -> writing "
                    "UNTAGGED" % (OWNED_TAG, e))

    def _tags(self, use_tag):
        # Omit the tag when ensure_tag() proved it unusable (_tag_ok is False),
        # so a missing/unviewable tag degrades to untagged writes instead of
        # 400-failing every PATCH. _tag_ok None = unchecked -> legacy behaviour
        # (include the tag).
        if use_tag and self._tag_ok is not False:
            return [{"slug": OWNED_TAG}]
        return None


def _merge(desired, use_tag, nb):
    payload = {k: v for k, v in desired.items() if v is not None}
    tags = nb._tags(use_tag)
    if tags:
        payload["tags"] = tags
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
        tags = nb._tags(use_tag)
        if tags:
            diff["tags"] = tags
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
        tags = nb._tags(use_tag)
        if tags:
            diff["tags"] = tags
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
            tags = nb._tags(use_tag)
            if tags:
                body["tags"] = tags
            nb._req("POST", "/api/ipam/vlans/", body)
        return action
    diff = _changed(existing, desired)
    if not diff:
        return "no-op vlan vid=%s (already matches)" % vid
    action = "update vlan vid=%s: %s" % (vid, diff)
    if apply:
        tags = nb._tags(use_tag)
        if tags:
            diff["tags"] = tags
        nb._req("PATCH", "/api/ipam/vlans/%d/" % existing["id"], diff)
    return action


def verify_ip(nb, address):
    """Read-back verification after an apply."""
    return nb.get_first("/api/ipam/ip-addresses/", {"address": address})
