"""rick-gonic-mcp — thin, read-only Subsonic/OpenSubsonic MCP for gonic.

This is the TASTE-SIGNAL read path for the `rick` music-recommendation operator.
gonic speaks only the Subsonic API (query-param auth: u + token/salt), so the
header-bridge (ckanthony/openapi-mcp) used for the other concierge MCPs does not
fit — hence this small bespoke wrapper. It exposes ONLY read methods; there is no
scrobble / star / setRating / startScan path by construction, so rick's prime
directive ("the taste signal is read-only") is enforced here at the tool surface,
not merely by behavioral guardrails.

The taste signal spans TWO gonic accounts that are both Robert's — `admin` and
`mass` (MA/WiiM scrobbles land in one, the phone client in the other). gonic
tracks play counts and stars PER USER, so a reader must query both accounts and
union the result; a single account (or a fresh dedicated user) only ever sees
half (or none) of the signal. Every tool defaults to account="both" and unions
in the reader — it never mutates gonic to merge counts.

Transport: Streamable HTTP at /mcp (FastMCP). Reached intra-namespace by the
kagent `rick` agent via a RemoteMCPServer CR.

Credentials come from the environment (populated by the ExternalSecret):
  GONIC_ADMIN_USER / GONIC_ADMIN_PASS   — the `admin` account
  GONIC_MASS_USER  / GONIC_MASS_PASS    — the `mass` account
  GONIC_URL                             — default http://gonic.media.svc.cluster.local:80
An account whose user/pass are empty is skipped, so the server still runs if only
one account is populated.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

GONIC_URL = os.environ.get("GONIC_URL", "http://gonic.media.svc.cluster.local:80").rstrip("/")
API_VERSION = "1.16.1"
CLIENT_NAME = "rick-gonic-mcp"
HTTP_TIMEOUT = 15.0


def _accounts() -> dict[str, dict[str, str]]:
    """Return the configured {label: {"u": user, "p": pass}} accounts (non-empty only)."""
    out: dict[str, dict[str, str]] = {}
    for label, u_env, p_env in (
        ("admin", "GONIC_ADMIN_USER", "GONIC_ADMIN_PASS"),
        ("mass", "GONIC_MASS_USER", "GONIC_MASS_PASS"),
    ):
        user = os.environ.get(u_env, "").strip()
        pw = os.environ.get(p_env, "").strip()
        if user and pw:
            out[label] = {"u": user, "p": pw}
    return out


ACCOUNTS = _accounts()


def _auth_params(creds: dict[str, str]) -> dict[str, str]:
    """Subsonic token auth: t = md5(password + salt). Keeps the plaintext
    password out of the request URL / any access log."""
    salt = secrets.token_hex(8)
    token = hashlib.md5((creds["p"] + salt).encode("utf-8")).hexdigest()
    return {
        "u": creds["u"],
        "t": token,
        "s": salt,
        "v": API_VERSION,
        "c": CLIENT_NAME,
        "f": "json",
    }


def _resolve_accounts(account: str) -> list[str]:
    if account == "both":
        return list(ACCOUNTS.keys())
    if account in ACCOUNTS:
        return [account]
    return []


async def _call(method: str, creds: dict[str, str], params: dict[str, Any]) -> dict[str, Any]:
    """Issue one Subsonic GET and return its `subsonic-response` body (or an error dict)."""
    q = _auth_params(creds)
    q.update({k: str(v) for k, v in params.items() if v is not None})
    url = f"{GONIC_URL}/rest/{method}.view"
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        try:
            resp = await client.get(url, params=q)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            return {"status": "error", "error": {"message": f"transport: {exc}"}}
    try:
        body = resp.json().get("subsonic-response", {})
    except ValueError:
        return {"status": "error", "error": {"message": "non-JSON response"}}
    return body


async def _gather(method: str, account: str, params: dict[str, Any]) -> dict[str, Any]:
    """Call `method` against each resolved account; return {account: subsonic-response}."""
    labels = _resolve_accounts(account)
    if not labels:
        return {"_meta": {"error": f"no configured gonic account for account={account!r}; "
                                   f"configured: {list(ACCOUNTS.keys()) or 'NONE'}"}}
    results: dict[str, Any] = {}
    for label in labels:
        results[label] = await _call(method, ACCOUNTS[label], params)
    return results


mcp = FastMCP("rick-gonic", host="0.0.0.0", port=8099)


@mcp.tool()
async def list_accounts() -> dict[str, Any]:
    """List which gonic taste-signal accounts are configured and reachable.

    Returns each configured account label and the result of a lightweight
    authenticated ping, so rick can confirm the signal source before reasoning.
    """
    out: dict[str, Any] = {"gonic_url": GONIC_URL, "accounts": {}}
    for label, creds in ACCOUNTS.items():
        body = await _call("ping", creds, {})
        out["accounts"][label] = {"status": body.get("status", "unknown")}
    if not ACCOUNTS:
        out["error"] = ("no gonic accounts configured — GONIC_ADMIN_* / GONIC_MASS_* "
                        "are empty (create the `gonic` 1Password item)")
    return out


@mcp.tool()
async def most_played_albums(count: int = 20, offset: int = 0, account: str = "both") -> dict[str, Any]:
    """Most-played albums — the PRIMARY taste signal (play-count ranked).

    Wraps getAlbumList2?type=frequent. `account` is "both" (default, unions the
    two accounts), "admin", or "mass". Union is in-reader; gonic is not mutated.
    """
    raw = await _gather("getAlbumList2", account, {"type": "frequent", "size": count, "offset": offset})
    return {"type": "frequent", "by_account": _extract_album_lists(raw)}


@mcp.tool()
async def recently_played_albums(count: int = 20, offset: int = 0, account: str = "both") -> dict[str, Any]:
    """Recently-played albums — the RECENCY signal (taste drifts; follow it).

    Wraps getAlbumList2?type=recent.
    """
    raw = await _gather("getAlbumList2", account, {"type": "recent", "size": count, "offset": offset})
    return {"type": "recent", "by_account": _extract_album_lists(raw)}


@mcp.tool()
async def get_starred(account: str = "both") -> dict[str, Any]:
    """Starred (favorited) artists/albums/songs — the SPARSE, high-precision signal.

    Wraps getStarred2. Robert rarely stars, so treat presence as a strong positive
    and absence as NOT a negative.
    """
    raw = await _gather("getStarred2", account, {})
    out: dict[str, Any] = {}
    for label, body in raw.items():
        if label == "_meta":
            out[label] = body
            continue
        starred = body.get("starred2", {}) if isinstance(body, dict) else {}
        out[label] = {
            "artists": starred.get("artist", []),
            "albums": starred.get("album", []),
            "songs": starred.get("song", []),
        }
    return out


@mcp.tool()
async def search(query: str, artist_count: int = 10, album_count: int = 20,
                 song_count: int = 30, account: str = "both") -> dict[str, Any]:
    """Search artists / albums / songs by name. Wraps search3.

    Use to dedupe a candidate against the library before recommending, and to
    resolve names to ids. Song hits carry per-user playCount.
    """
    raw = await _gather("search3", account, {
        "query": query, "artistCount": artist_count,
        "albumCount": album_count, "songCount": song_count,
    })
    out: dict[str, Any] = {}
    for label, body in raw.items():
        if label == "_meta":
            out[label] = body
            continue
        res = body.get("searchResult3", {}) if isinstance(body, dict) else {}
        out[label] = {
            "artists": res.get("artist", []),
            "albums": res.get("album", []),
            "songs": res.get("song", []),
        }
    return out


@mcp.tool()
async def get_album(album_id: str, account: str = "both") -> dict[str, Any]:
    """Album detail incl. its tracks with per-user playCount. Wraps getAlbum.

    The track-level play counts here are the granular taste signal behind an
    album; use after most_played_albums to see WHICH tracks drive a high count.
    """
    raw = await _gather("getAlbum", account, {"id": album_id})
    out: dict[str, Any] = {}
    for label, body in raw.items():
        if label == "_meta":
            out[label] = body
            continue
        out[label] = body.get("album", {}) if isinstance(body, dict) else {}
    return out


@mcp.tool()
async def get_song(song_id: str, account: str = "both") -> dict[str, Any]:
    """Single song detail incl. playCount and starred state. Wraps getSong."""
    raw = await _gather("getSong", account, {"id": song_id})
    out: dict[str, Any] = {}
    for label, body in raw.items():
        if label == "_meta":
            out[label] = body
            continue
        out[label] = body.get("song", {}) if isinstance(body, dict) else {}
    return out


def _extract_album_lists(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, body in raw.items():
        if label == "_meta":
            out[label] = body
            continue
        if isinstance(body, dict) and body.get("status") == "error":
            out[label] = {"error": body.get("error", {}).get("message", "unknown")}
            continue
        albums = body.get("albumList2", {}).get("album", []) if isinstance(body, dict) else []
        out[label] = albums
    return out


if __name__ == "__main__":
    # Streamable HTTP; FastMCP serves the MCP endpoint at /mcp on :8099.
    mcp.run(transport="streamable-http")
