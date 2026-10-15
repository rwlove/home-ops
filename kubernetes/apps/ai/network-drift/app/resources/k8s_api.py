#!/usr/bin/env python3
"""Phase 2 collectors: live Kubernetes / Cilium reality (read-only, stdlib).

Reads nodes, LoadBalancer Service IPs, and Cilium LB IP pools straight from the
kube-apiserver using the pod's own scoped ServiceAccount token (a real RBAC
boundary — get/list on exactly nodes + services + ciliumloadbalancerippools, see
rbac.yaml). No kubectl, no MCP broker. Diffs are one-directional (reality present
but NetBox record absent) to stay low-noise; every proposal is HUMAN-ONLY.

BGP peering health is intentionally NOT collected here: it is runtime health, not
NetBox inventory — it belongs to the network-triage watcher, not this mirror.
"""
import json
import os
import ssl
import urllib.request

import netbox_sync as ns

_SA = "/var/run/secrets/kubernetes.io/serviceaccount"


def _ctx():
    return ssl.create_default_context(cafile=os.path.join(_SA, "ca.crt"))


def _token():
    with open(os.path.join(_SA, "token"), encoding="utf-8") as f:
        return f.read().strip()


def _get(path):
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    url = "https://%s:%s%s" % (host, port, path)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer %s" % _token()})
    with urllib.request.urlopen(req, timeout=30, context=_ctx()) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# -- collectors --------------------------------------------------------------
def collect_nodes():
    out = []
    for item in _get("/api/v1/nodes").get("items", []):
        name = item.get("metadata", {}).get("name")
        ips = [a.get("address") for a in item.get("status", {}).get("addresses", [])
               if a.get("type") == "InternalIP"]
        out.append({"name": name, "ips": [i for i in ips if i]})
    return out


def collect_lb_service_ips():
    out = []
    data = _get("/api/v1/services")
    for svc in data.get("items", []):
        if svc.get("spec", {}).get("type") != "LoadBalancer":
            continue
        meta = svc.get("metadata", {})
        for ing in svc.get("status", {}).get("loadBalancer", {}).get("ingress", []):
            ip = ing.get("ip")
            if ip:
                out.append({"ip": ip, "svc": "%s/%s" % (meta.get("namespace"),
                                                        meta.get("name"))})
    return out


def collect_lb_pools():
    out = []
    data = _get("/apis/cilium.io/v2/ciliumloadbalancerippools")
    for item in data.get("items", []):
        name = item.get("metadata", {}).get("name")
        for block in item.get("spec", {}).get("blocks", []) or []:
            cidr = block.get("cidr")
            if cidr:
                out.append({"pool": name, "cidr": ns._norm_subnet(cidr)})
    return out


# -- comparators (one-directional: reality -> NetBox record) -----------------
def compare_nodes(nodes, nb_ips, nb_devices):
    drifts = []
    nb_ip_set = {ns._bare_ip(p["address"]) for p in nb_ips if p.get("address")}
    nb_names = {ns._norm_name(d["name"]) for d in nb_devices if d.get("name")}
    for n in nodes:
        nm = ns._norm_name(n["name"])
        ip_known = any(ns._bare_ip(ip) in nb_ip_set for ip in n["ips"])
        if nm not in nb_names and not ip_known:
            drifts.append(ns.Drift(
                "k8s-node-missing-in-netbox", "node=%s" % nm,
                reality="k8s node '%s' (%s) is live" % (n["name"], ", ".join(n["ips"])),
                record="no matching device/IP in NetBox",
                proposal="HUMAN-ONLY: add node '%s' (device+interface+IP) to NetBox"
                         % n["name"]))
    return drifts


def compare_lb_ips(lb_ips, nb_ips):
    drifts = []
    nb_ip_set = {ns._bare_ip(p["address"]) for p in nb_ips if p.get("address")}
    for lb in lb_ips:
        if ns._bare_ip(lb["ip"]) not in nb_ip_set:
            drifts.append(ns.Drift(
                "lb-ip-missing-in-netbox", "ip=%s" % lb["ip"],
                reality="LoadBalancer IP %s is assigned to %s" % (lb["ip"], lb["svc"]),
                record="no ip-address %s in NetBox" % lb["ip"],
                proposal="HUMAN-ONLY: add ip-address %s (svc %s) to NetBox"
                         % (lb["ip"], lb["svc"]),
                # SAFE auto-write target (Phase 4): create the ip-address for this
                # LoadBalancer IP (NetBox stores a bare host IP as /32).
                target={"kind": "ip", "address": lb["ip"],
                        "description": "LB svc %s" % lb["svc"]}))
    return drifts


def compare_lb_pools(pools, nb_prefixes):
    drifts = []
    nb_subnets = {p["prefix"] for p in nb_prefixes if p.get("prefix")}
    for p in pools:
        if p["cidr"] and p["cidr"] not in nb_subnets:
            drifts.append(ns.Drift(
                "lb-pool-missing-in-netbox", "cidr=%s" % p["cidr"],
                reality="Cilium LB pool '%s' advertises %s" % (p["pool"], p["cidr"]),
                record="no matching prefix in NetBox",
                proposal="HUMAN-ONLY: add prefix %s (LB pool '%s') to NetBox"
                         % (p["cidr"], p["pool"])))
    return drifts


def run(nb_ips, nb_prefixes, nb_devices):
    """Collect + compare. Returns (drifts, findings). A failed read of any one
    k8s source is a single source-finding, not a crash."""
    drifts, findings = [], []
    try:
        nodes = collect_nodes()
        drifts += compare_nodes(nodes, nb_ips, nb_devices)
        ns.log("k8s: %d nodes" % len(nodes))
    except Exception as e:  # noqa: BLE001
        findings.append(("k8s-nodes", str(e)))
    try:
        lb_ips = collect_lb_service_ips()
        drifts += compare_lb_ips(lb_ips, nb_ips)
        ns.log("k8s: %d LoadBalancer service IPs" % len(lb_ips))
    except Exception as e:  # noqa: BLE001
        findings.append(("k8s-lb-services", str(e)))
    try:
        pools = collect_lb_pools()
        drifts += compare_lb_pools(pools, nb_prefixes)
        ns.log("cilium: %d LB pool block(s)" % len(pools))
    except Exception as e:  # noqa: BLE001
        findings.append(("cilium-lb-pools", str(e)))
    return drifts, findings
