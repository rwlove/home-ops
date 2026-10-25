# rick-library-mcp

A write-capable MCP server that lets the kagent **rick** agent — the ward of the
music library — remediate misfiled content: an album that groups under the wrong
artist in the player because its album-artist **text** tag is corrupt (the player
groups by that text, not by MusicBrainz id).

Bound cross-namespace to `rick` as a `RemoteMCPServer`
(`ai/kagent/app/remotemcpserver-rick-library-mcp.yaml`). Streamable-HTTP on
`:8095/mcp`.

## Safety model (non-negotiable)

Every mutation this server performs is:

- **Backed up first** — the original file is copied to an **off-library** volume
  (`/revert`, a separate ceph-block PVC, never under the library) before it is
  touched. Fully revertible.
- **Recorded** — appended to a durable `ledger.jsonl` on that volume and returned
  to the agent (which also mirrors it to its memory).
- **Confidence-gated** — only HIGH-confidence fixes apply without `confirmed=true`.
- **Never a delete** — there is no delete tool. Fixes are tag edits (and, later,
  moves); both reversible.

## Tools

| Tool | Write? | Purpose |
|------|--------|---------|
| `evaluate` / `propose` | no | Assess one album (its folder + ARTIST + album-artist signals), return a confidence verdict (HIGH / PROPOSE / SUPPRESS / SKIP) and the recommended fix. Library-wide-aware. |
| `apply` | **yes** | Backup-first, set the album-artist, record to the ledger. Refuses below HIGH unless `confirmed=true`. |
| `revert` | **yes** | Restore the originals from the backup volume for a given `change_id`. |
| `history` | no | Query the change ledger. |
| `rescan` | no\* | Trigger a player library rescan so applied changes surface. (\*calls gonic; no file write.) |

## Confidence model

- **HIGH** — every track's ARTIST tag agrees with the folder, only `album_artist`
  is the odd one out (an unambiguous corrupt label). Auto-eligible.
- **PROPOSE** — folder + label agree but ARTIST is a different artist (cover or
  whole-album misfile), or a stale/renamed folder, or signals conflict. Judgement
  required; never auto-applied.
- **SUPPRESS** — folder already agrees with the label (incl. non-Latin/symbol
  names), or a known alias (`ALIAS_PAIRS`).

## Config

Env on the HelmRelease: `MEDIA_ROOT=/media` (RW), `BACKUP_ROOT=/revert`,
`PORT=8095`, `GONIC_URL` (+ `GONIC_ADMIN_USER/PASS` from the `gonic` 1Password
item, used only by `rescan`). `ALIAS_PAIRS` is intentionally **unset** here:
artist names are restricted-tier (HOMELAB-SPEC L2 #10) and never committed —
supply the alias list at runtime from a secret to quiet benign rename mismatches.

## Autonomy (staged)

Ships with `rick`'s prime directive = **propose-only / approval-gated** for every
write. Once proven on the known backlog under explicit invocation, a directive
toggle enables auto-apply of **HIGH-confidence** fixes on the weekly audit cadence
(the `beets-misfile-audit` sweep feeds the candidate list). Ambiguous fixes stay
proposals permanently.

Part of the music-library integrity work under Rick's ward role.
