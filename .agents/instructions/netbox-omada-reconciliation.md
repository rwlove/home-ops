# NetBox ↔ Omada reconciliation — every write, not just DNS/DHCP

Applies to the **network-operator** persona (`~/.claude-personal/agents/network-operator.md`)
and the Lovenet config repo (`lovenet-network-configuration`). Filed here because this
session's repo work was scoped to the active home-ops worktree; the canonical home for this
rule is the network-operator persona doc and/or `lovenet-network-configuration/docs/`, and it
should be mirrored there when next touched.

## The rule

**Any live Omada write that changes a switch port's name, profile, or VLAN — not just the
netop-foothold DNS/DHCP flow — must be followed, in the same session, by updating the
corresponding NetBox `Interface` record** (`description`, `untagged_vlan`, and the
`omada_profile` custom field) to match the new live state. This extends the existing
"reconcile NetBox after every netop-foothold DNS/DHCP write" rule (see the network-operator
persona's "netop foothold" section) to **ad-hoc Omada port writes** — profile moves, port
renames, VLAN retags, decommissions — which previously had no equivalent forcing function.

## Why this exists

NetBox's `switch-core` / `switch-poe` interfaces carry a hand-built mirror of each port's
Omada profile (`description: "<name> (Omada profile: <profile>)"` + `untagged_vlan`). This is
a good pattern — it's the only place physical-port-to-VLAN mapping is queryable outside the
Omada UI — but it rotted silently:

- `switch-poe` Port2 was decommissioned from `amcrest-pool` and reassigned to a `reolink-pool`
  camera (Security profile, verified live via the Omada OpenAPI) on 2026-10-06. NetBox still
  described it as `amcrest-pool (Omada profile: Security)` as of 2026-10-15 — a 9-day silent
  drift, discovered only because of an unrelated NetBox usage assessment.
- Five more ports on the same two switches (`Port11`/wiim-deck, `Port13`/profile drifted to
  Security, `Port16`/gym-front, `Port17`/ap-frontyard, `Port21`+`Port22`/wiim-yard+wiim-pool
  VLAN never updated to IOT) had the same kind of drift.
- `switch-core` Port18 (live Omada: `Hue Bridge`, IOT profile) had **no NetBox Interface
  record at all** — not stale, simply never created.

None of this was caught by any process, alert, or habit — it was only found by accident during
a one-off audit. That's the gap this rule closes: a mirror with no forcing function to stay
current is worse than no mirror, because it's trusted at face value.

## What "reconcile" means in practice

1. After any Omada port write (profile change, rename, VLAN retag, decommission-to-`All`):
   - `GET` the switch's live port state via the Omada OpenAPI
     (`/openapi/v1/{cid}/sites/{sid}/switches/{mac}` → `.result.portList`).
   - `PATCH` the matching NetBox `dcim.interface` record's `description`
     (`"<live name> (Omada profile: <live profile>)"`), `untagged_vlan` (map profile → VLAN:
     `All`→Default, `Security`→Security, `IOT`→IOT, `Guest`→Guest), and the `omada_profile`
     custom field to the live value.
   - If no NetBox Interface record exists for that port yet, create one rather than leaving
     the gap (same pattern as the `switch-core` Port18 backfill).
2. This is in addition to — not instead of — the existing netop-foothold DNS/DHCP
   reconciliation (brain → NetBox → Git → Omada, see the network-operator persona's "After
   every foothold add/remove, reconcile ALL FOUR sources of truth" section). Port-level writes
   that aren't DNS/DHCP changes (e.g. a pure profile/VLAN move with no new reservation) still
   need the NetBox Interface side reconciled even though the foothold script doesn't touch them.
3. Scope: this applies to switches where the Omada-profile mirror pattern is already adopted
   (`switch-core`, `switch-poe` today). Extending the pattern to the other switches
   (`switch-basement`, `switch-office`, `switch-familyroom`, `switch-10`) is a separate,
   larger modeling decision — not implied by this rule, but a natural next step once those
   switches carry equivalent custom-named ports worth mirroring.

## What this is NOT

- Not a mandate to model every physical port — generic unnamed `PortN` ports with no live
  Omada customization don't need an individual reconciliation pass; this rule is about keeping
  *already-modeled* ports from drifting, not expanding the modeled surface on every touch.
- Not a substitute for the `docs/netop-change-log.md` ledger in `lovenet-network-configuration`
  — that's the dated, narrative change record; this NetBox reconciliation is the live-state
  mirror. Both are required after a write that touches both.
- Not a guarantee NetBox is real-time-accurate — it's reconciled *at write time*, not
  continuously. A drift-detection sweep (comparing live Omada state against NetBox on a
  schedule) would close that gap further but is a separate project (see the webhooks/
  event-rules proposal in the NetBox usage assessment, 2026-10-15).
