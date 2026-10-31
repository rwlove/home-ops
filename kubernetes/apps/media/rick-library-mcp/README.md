# rick-library-mcp

A write-capable MCP server that lets the kagent **rick** agent — the ward of the
music library — remediate misfiled content: an album grouped under the wrong
artist in the player (the player groups by the album-artist **text** tag), or
whole content sitting in the wrong artist's folder.

Identity is decided by **MusicBrainz**, the authority on artist identity — so
renames/aliases (two names, one artist) and genuine wrong-content are
distinguished by fact, not a hand-maintained list or a text guess.

Bound cross-namespace to `rick` as a `RemoteMCPServer`
(`ai/kagent/app/remotemcpserver-rick-library-mcp.yaml`). Streamable-HTTP on
`:8095/mcp`.

## Safety model (non-negotiable)

Every mutation is:

- **Backed up first** — the original file is copied to an **off-library** volume
  (`/revert`, a separate ceph-block PVC, never under the library) before it is
  touched. Fully revertible.
- **Recorded** — appended to a durable `ledger.jsonl` on that volume, returned to
  the agent (which mirrors it to memory).
- **Pre-flighted** — every file must be writable before any is touched, so a
  change can never half-apply.
- **Never a delete** — the only mutations are tag edits and moves, both reversible.

## Tools

| Tool | Write? | Purpose |
|------|--------|---------|
| `evaluate` / `propose` | no | Assess one album using MusicBrainz as the identity authority; return per-track signals, resolved MB ids, and a recommended **action**. |
| `apply` | **yes** | The `retag` action — set the album-artist to the folder name (content *is* the folder artist, label is corrupt). Backup + ledger. |
| `relocate` | **yes** | The `relocate` action — move an album whose content is a *different* artist to that artist's folder + fix tags. Backup + ledger; never overwrites an existing target. |
| `revert` | **yes** | Undo a `change_id` — restore tags (retag) or move the album back + restore tags (relocate). |
| `history` | no | Query the change ledger. |
| `rescan` | no\* | Trigger a player library rescan so changes surface. (\*calls gonic; no file write.) |

## Action model (MusicBrainz-authoritative)

`evaluate` decides identity from exact MusicBrainz ids — the album's embedded
artist MBID vs. the folder's canonical MBID, which is **derived from that folder's
other albums** (library-authoritative). Fuzzy name search is a last resort for the
folder and only when the top hit is an *exact* name match; relocate targets are
named by an exact lookup *by MBID*. It recommends:

- **retag** — the content *is* the folder artist; only `album_artist` is corrupt →
  set it to the folder name.
- **relocate** — the content is a *different* MB artist → move the album to that
  artist's folder.
- **leave** — the names are the same MB entity (an alias/rename), a compilation,
  or already correct.
- **skip** — no audio.

When MusicBrainz can't confidently resolve identity, it degrades to a **safe text
heuristic**: retag the clear corrupt-label case, else leave — it **never
relocates on a guess**.

## Config

Env on the HelmRelease: `MEDIA_ROOT=/media` (RW), `BACKUP_ROOT=/revert`,
`PORT=8095`, `GONIC_URL` (+ `GONIC_ADMIN_USER/PASS` from the `gonic` 1Password
item, used only by `rescan`). No artist names in config — identity is MusicBrainz.
The MB API needs only a User-Agent (hardcoded); egress is the existing `world:443`
rule, and lookups are cached to `/revert/mb-cache.json`.

## Autonomy

The autonomy level is set by `rick`'s directive in
`ai/kagent/app/agent-rick.yaml`, not here — the server just exposes the
capabilities (with the safety substrate baked in). The weekly `beets-misfile-audit`
sweep feeds the candidate list to the agent.

Part of the music-library integrity work under Rick's ward role.
