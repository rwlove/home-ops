#!/usr/bin/env python3
"""Phase 4 — gated auto-write of the SAFE drift residual. SHIPS OFF.

This is the ONLY component of the NetBox-freshness system that can write NetBox
unattended, and it is OFF by default three times over:

  1. Its CronJob (cronjob-upsert.yaml) ships `suspend: true` — it never runs.
  2. THIS code writes nothing unless `AUTOWRITE_APPLY=true` (default "false").
     With it false the run is a DRY-RUN: it logs/emails what it WOULD upsert.
  3. The write-scoped NetBox token (externalsecret-write.yaml) does not exist in
     1Password until Rob creates it, so the ExternalSecret is NotReady and the
     Job cannot even start.

>>> THE ONE TOGGLE THAT ENABLES AUTO-WRITE IS `AUTOWRITE_APPLY` <<<
Everything else (unsuspend, token) is prerequisite plumbing; the actual
permission to mutate NetBox is this single env, and it DEFAULTS OFF.

Scope — intentionally NARROW. Only the two genuinely-safe, additive/
non-destructive, owned categories are ever auto-written:

  * netbox-dns-name-stale   -> PATCH dns_name on an EXISTING ip-address
  * lb-ip-missing-in-netbox -> CREATE the ip-address for a LoadBalancer IP

Never VLAN renames, prefix edits, device creation, or anything touching the
mgmt LAN (192.168.x) or the k8s internal ranges (10.42/43/45) — those stay
detect-only / human (the recovery-path floor). Broadening this set, or swapping
the upsert backend to NetBox **Diode** (the design's intended upsert layer, which
avoids brittle GET->diff->PATCH), is a later, deliberate step once the Diode
server is stood up — see UPSERT_BACKEND below and the README.

The non-safe residual is where the gated **LLM/kagent** classification leg would
run (LLM_RESIDUAL_ENABLED, default off) — it is a documented seam here, not wired
to a model in this phase.

Stdlib only.
"""
import datetime
import os
import sys

import netbox_sync as ns
import netbox_writeback as wb

AUTOWRITE_APPLY = os.environ.get("AUTOWRITE_APPLY", "false").lower() == "true"
UPSERT_BACKEND = os.environ.get("UPSERT_BACKEND", "writeback").lower()
LLM_RESIDUAL_ENABLED = os.environ.get("LLM_RESIDUAL_ENABLED", "false").lower() == "true"

# Narrow allowlist — the only categories ever eligible for auto-write.
SAFE_CATEGORIES = {"netbox-dns-name-stale", "lb-ip-missing-in-netbox"}

# Recovery-path floor: never auto-write these ranges, regardless of category.
EXCLUDED_PREFIXES = ("192.168.", "10.42.", "10.43.", "10.45.")


def excluded(address):
    bare = ns._bare_ip(address) or ""
    return any(bare.startswith(p) for p in EXCLUDED_PREFIXES)


def apply_one(nb, d, do_write):
    t = d.target or {}
    if t.get("kind") != "ip" or not t.get("address"):
        return "skip %s (no writable ip target)" % d.fingerprint
    addr = t["address"]
    if excluded(addr):
        return "SKIP %s %s (recovery-path floor: protected range)" % (d.category, addr)
    if t.get("dns_name"):
        action = wb.upsert_ip(nb, addr, dns_name=t["dns_name"], apply=do_write)
    else:
        action = wb.upsert_ip(nb, addr, description=t.get("description"), apply=do_write)
    if do_write:
        got = wb.verify_ip(nb, addr)
        action += " [verified]" if got is not None else " [VERIFY FAILED]"
    return action


def main():
    day = datetime.date.today().isoformat()
    if UPSERT_BACKEND == "diode" and not os.environ.get("DIODE_TARGET"):
        ns.log("UPSERT_BACKEND=diode but DIODE_TARGET is unset — Diode server not "
               "deployed. Refusing to proceed; nothing written.")
        return 0
    if UPSERT_BACKEND not in ("writeback", "diode"):
        ns.log("unknown UPSERT_BACKEND=%s; nothing written" % UPSERT_BACKEND)
        return 0

    eps = ns.endpoints()
    drifts, _ = ns.compute_drifts(eps)

    acked = ns.load_acked()
    safe = [d for d in drifts
            if d.category in SAFE_CATEGORIES and d.target
            and d.fingerprint not in acked]
    residual = [d for d in drifts
                if d.category not in SAFE_CATEGORIES
                and d.category != "source-unreadable"
                and d.fingerprint not in acked]

    # Token presence is itself a gate: without a write token we cannot apply even
    # if AUTOWRITE_APPLY is true — degrade to dry-run rather than erroring.
    have_token = bool(os.environ.get("NETBOX_TOKEN"))
    do_write = AUTOWRITE_APPLY and have_token
    mode = "APPLY" if do_write else "DRY-RUN"
    if AUTOWRITE_APPLY and not have_token:
        ns.log("AUTOWRITE_APPLY=true but NETBOX_TOKEN is empty — forcing DRY-RUN")

    ns.log("autowrite %s: %d safe candidate(s), %d non-safe residual, backend=%s"
           % (mode, len(safe), len(residual), UPSERT_BACKEND))

    actions = []
    if safe:
        nb = wb.NetBox()  # NETBOX_URL required; token optional in dry-run
        for d in sorted(safe, key=lambda x: (x.category, x.key)):
            try:
                act = apply_one(nb, d, do_write)
            except wb.WritebackError as e:
                act = "FAILED %s: %s" % (d.fingerprint, e)
            actions.append(act)
            ns.log("  %s" % act)

    # LLM/kagent residual classification — documented gated seam (off here).
    if LLM_RESIDUAL_ENABLED and residual:
        ns.log("LLM residual leg ENABLED but not wired to a model in this phase — "
               "%d item(s) would route to the kagent network-operator for "
               "classification via the agent-ops queue" % len(residual))
    elif residual:
        ns.log("LLM residual leg disabled — %d non-safe item(s) stay detect-only "
               "/ human" % len(residual))

    # Email a summary only when there is something to report.
    if safe:
        head = ("APPLIED" if do_write else "DRY-RUN (no change; set "
                "AUTOWRITE_APPLY=true to enable)")
        lines = ["FYI: NetBox-freshness **auto-write** — %s (%s)." % (head, day), "",
                 "Safe residual upserts (%d):" % len(safe)]
        lines += ["- `%s`" % a for a in actions[:ns.DIGEST_ITEM_CAP]]
        if len(actions) > ns.DIGEST_ITEM_CAP:
            lines.append("- _…and %d more (job logs)._"
                         % (len(actions) - ns.DIGEST_ITEM_CAP))
        if residual:
            lines.append("")
            lines.append("_%d non-safe item(s) left for human/Loop-A review._"
                         % len(residual))
        ns.notify("netbox-sync auto-write — %s" % day, "\n".join(lines)[:3500])
    else:
        ns.log("no safe residual — nothing to upsert, no email")

    ns.log("AUTOWRITE_RESULT=mode=%s safe=%d residual=%d applied=%s"
           % (mode, len(safe), len(residual), do_write))
    return 0


if __name__ == "__main__":
    sys.exit(main())
