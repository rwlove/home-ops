"""NetBox internal-consistency validator (NetBox Script — runs INSIDE NetBox).

Per the design, NetBox **Reports/Scripts** own internal-consistency validation
(the external collectors live in the ai/network-drift sweep, not here). This
Script is READ-ONLY: it logs findings, it never writes. It flags things that are
wrong *within NetBox itself*, independent of live reality:

- duplicate IP addresses (same address on more than one record)
- prefixes with no VLAN association
- VLANs with no prefix
- ip-addresses whose dns_name is empty

INSTALL (operator, inside NetBox — not auto-applied by Flux): place this file in
NetBox's SCRIPTS_ROOT (or upload via Operations > Scripts), then run it on demand
or on a schedule. It uses NetBox's ORM, so it only imports/executes within the
NetBox app context — it is intentionally NOT wired into any cluster workload.
"""
from django.db.models import Count

from extras.scripts import Script
from ipam.models import IPAddress, Prefix, VLAN


class NetworkConsistencyReport(Script):
    class Meta:
        name = "Network consistency"
        description = "Read-only internal-consistency checks for IPAM data."

    def run(self, data, commit):  # noqa: ARG002 - NetBox Script signature
        dupes = (IPAddress.objects.values("address")
                 .annotate(n=Count("id")).filter(n__gt=1))
        for row in dupes:
            self.log_warning("duplicate IP address: %s (x%d)"
                             % (row["address"], row["n"]))

        for p in Prefix.objects.filter(vlan__isnull=True):
            self.log_info("prefix %s has no VLAN association" % p.prefix)

        for v in VLAN.objects.annotate(n=Count("prefixes")).filter(n=0):
            self.log_info("VLAN %s (%s) has no prefix" % (v.vid, v.name))

        missing_dns = IPAddress.objects.filter(dns_name="").count()
        if missing_dns:
            self.log_info("%d ip-address record(s) have an empty dns_name"
                          % missing_dns)

        self.log_success("consistency scan complete (read-only; nothing changed)")
