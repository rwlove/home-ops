# beets-misfile-audit

Read-only weekly audit that detects **misfiled music directories** — content
that lives under the wrong artist in the library and therefore groups under the
wrong artist in the player. Report-only: it never moves, deletes, tags, or
writes anything. Findings are emailed as one digest via `notify-mcp` and also
printed to the Job log.

Ships **suspended** — it is manual-tested before each unsuspend (below).

## Why

The library occasionally accumulates misfiles two ways:

1. **Content misfile.** The unattended acquisition pipeline grabs a same-titled
   track by the *wrong* artist (thin-seeded niche artists), and the library
   manager files it under the *intended* artist's folder — right folder, wrong
   audio. Surfaces as a one-track "album" by the wrong artist.
2. **Album-level tag misgroup.** A whole album lands in the wrong artist's folder
   carrying an album-artist tag for a *different* artist (a loose folder+count
   match by the library manager, not a real metadata match). The player groups
   by album-artist identity, so the album then shows under the wrong artist.

## Two passes

| Pass | Signal | Scope | Cost |
|------|--------|-------|------|
| **1 — content** | AcoustID fingerprint (what the audio *is*) vs the artist folder | single-track dirs only | network; rate-limited |
| **2 — album tag** | embedded album-artist tag vs the artist folder | every album dir, any track count | offline (mutagen); fast |

Pass 2 was added after a 15-track album was found misfiled under the wrong
artist — pass 1 could never catch it because it only looks at single-track
dirs. Pass 2 suppresses the benign cases: various-artists / compilation buckets,
and known artist aliases/renames (see `ALIAS_PAIRS`).

## Configuration (HelmRelease `env`)

| Var | Default | Meaning |
|-----|---------|---------|
| `MEDIA_ROOT` | `/media` | library root (mounted read-only) |
| `LOOKBACK_DAYS` | `8` | pass 1: only recently-changed single-track dirs (0 = all) |
| `MIN_SCORE` | `0.90` | pass 1: minimum AcoustID score to trust a fingerprint |
| `ALBUM_LOOKBACK_DAYS` | `0` | pass 2: 0 = scan everything; raise to focus on new imports once the backlog is clear |
| `ALIAS_PAIRS` | *(unset)* | pass 2 noise control — see below |
| `NOTIFY_URL` | in-cluster `notify-mcp` `/send` | digest sink; empty = stdout only |
| `AGENT_NAME` | `rick` | notify-mcp sender/theme |

### `ALIAS_PAIRS` — artist aliases are supplied at runtime, never committed

Artist names are restricted-tier (HOMELAB-SPEC L2 #10) and must not appear in
this public repo. So the alias allowlist that suppresses benign rename
mismatches (an artist filed under an old name vs. tagged under a new one) is
**not** in git. Supply it at runtime from a secret / 1Password as a JSON array
of arrays of names that are the same act, e.g.
`[["name a","name b"],["name c","name d"]]`. Unset ⇒ no alias suppression
(benign renames show up as findings until the list is populated — expected on
the first runs).

## Test → unsuspend

```sh
# manual test run (reads logs; sends a digest if it finds anything)
kubectl -n media create job --from=cronjob/beets-misfile-audit beets-misfile-audit-test
kubectl -n media logs job/beets-misfile-audit-test -f
# when the output looks right, flip suspend:false in the HelmRelease
```

Part of the music-library integrity work tracked under Rick's steward role.
