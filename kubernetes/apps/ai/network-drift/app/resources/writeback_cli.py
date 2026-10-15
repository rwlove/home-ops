#!/usr/bin/env python3
"""Loop A write-back CLI — the one call the operator makes right after a live
mutation, to mirror it into NetBox in the same change.

SAFETY: dry-run is the DEFAULT. Nothing is written unless `--apply` is passed.
So shipping this code writes nothing; the operator opts in per invocation.

NOTE: --apply/--no-tag are TOP-LEVEL flags handled by the outer parser, not
the per-verb subparsers — they must come BEFORE the verb, not after (argparse
hands everything following the verb to that verb's own subparser, which
doesn't know --apply/--no-tag). Examples below corrected 2026-10-15 after
lovenet-network-configuration PR #77 hit "unrecognized arguments: --apply"
running the previous (wrong) order verbatim.

Examples (pair these with the live change you just made):
  # after `netop-dns add-host foo 10.10.30.9`:
  writeback_cli.py --apply ip-upsert 10.10.30.9/24 --dns-name foo.thesteamedcrab.com
  # after `netop-dns remove-host foo`:
  writeback_cli.py --apply ip-delete 10.10.30.9/24
  # after an Omada network/VLAN change:
  writeback_cli.py --apply vlan-upsert 30 IoT
  writeback_cli.py --apply prefix-upsert 10.10.30.0/24

Env: NETBOX_URL, NETBOX_TOKEN (operator's own token; Loop A needs write only on
ipam ip-addresses/prefixes/vlans). --no-tag skips the `netbox-sync` owned tag.
Stdlib only.
"""
import argparse
import sys

import netbox_writeback as wb


def main(argv=None):
    p = argparse.ArgumentParser(description="NetBox Loop A write-back (dry-run by default)")
    p.add_argument("--apply", action="store_true",
                   help="actually write NetBox (default: dry-run, print only)")
    p.add_argument("--no-tag", action="store_true",
                   help="do not apply the netbox-sync owned tag")
    sub = p.add_subparsers(dest="verb", required=True)

    s = sub.add_parser("ip-upsert")
    s.add_argument("address")
    s.add_argument("--dns-name")
    s.add_argument("--description")

    s = sub.add_parser("ip-delete")
    s.add_argument("address")

    s = sub.add_parser("prefix-upsert")
    s.add_argument("prefix")
    s.add_argument("--description")

    s = sub.add_parser("vlan-upsert")
    s.add_argument("vid", type=int)
    s.add_argument("name")

    args = p.parse_args(argv)
    use_tag = not args.no_tag
    try:
        nb = wb.NetBox()
        if args.verb == "ip-upsert":
            action = wb.upsert_ip(nb, args.address, args.dns_name, args.description,
                                  apply=args.apply, use_tag=use_tag)
        elif args.verb == "ip-delete":
            action = wb.delete_ip(nb, args.address, apply=args.apply)
        elif args.verb == "prefix-upsert":
            action = wb.upsert_prefix(nb, args.prefix, args.description,
                                      apply=args.apply, use_tag=use_tag)
        elif args.verb == "vlan-upsert":
            action = wb.upsert_vlan(nb, args.vid, args.name,
                                    apply=args.apply, use_tag=use_tag)
        else:  # unreachable (required subparser)
            p.error("unknown verb")
    except wb.WritebackError as e:
        print("WRITEBACK FAILED: %s" % e, file=sys.stderr)
        return 1

    mode = "APPLIED" if args.apply else "DRY-RUN (no change; pass --apply to write)"
    print("%s: %s" % (mode, action))

    # Read-back verification on an applied ip write.
    if args.apply and args.verb in ("ip-upsert", "ip-delete"):
        got = wb.verify_ip(nb, args.address)
        if args.verb == "ip-upsert" and got is None:
            print("VERIFY FAILED: %s not present after write" % args.address, file=sys.stderr)
            return 2
        if args.verb == "ip-delete" and got is not None:
            print("VERIFY FAILED: %s still present after delete" % args.address, file=sys.stderr)
            return 2
        print("verified: %s" % args.address)
    return 0


if __name__ == "__main__":
    sys.exit(main())
