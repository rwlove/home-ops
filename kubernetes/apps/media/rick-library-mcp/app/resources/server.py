#!/usr/bin/env python3
"""rick-library-mcp — bounded, reversible music-library remediation for the
kagent `rick` agent (the ward of the library).

Rick detects misfiled content — an album grouped under the WRONG artist because
its album-artist TEXT tag is corrupt (the player groups by that text, not by
MusicBrainz id) — and fixes it. Every mutation this server performs is:

  * BACKED UP FIRST — the original file is copied to an off-library volume
    (BACKUP_ROOT, a separate PVC, never under the library) before it is touched,
    so any change is fully revertible. (A careless delete with no backup is what
    caused real data loss; this server structurally cannot repeat that.)
  * RECORDED — appended to a durable JSONL ledger on the backup volume, and
    returned to the caller so the agent can also mirror it into its memory.
  * CONFIDENCE-GATED — only HIGH-confidence fixes apply without `confirmed=True`;
    anything ambiguous is returned as a proposal, never auto-applied.
  * NEVER A DELETE — there is no delete tool. Mutations are tag edits and moves;
    both are reversible.

Library-wide consistency: `evaluate()` reads the album, its artist folder, and the
candidate target folder so a fix converges on an existing canonical identity
instead of fragmenting the artist.

Confidence model:
  * HIGH  — every track's ARTIST tag relates to the folder, but `album_artist`
            is a different, internally-consistent value (the corruption). Fix =
            set `album_artist` to the folder/ARTIST canonical. Safe and unambiguous.
  * PROPOSE — tags internally consistent but the folder is stale (a rename), or
            ARTIST is a genuinely different artist (a cover), or signals conflict.
            Returned for human/agent judgement; never auto-applied.
  * SUPPRESS — folder already relates to `album_artist` (incl. non-Latin/symbol
            names), or the two sides are a known alias (ALIAS_PAIRS). Not a finding.

Env:
  MEDIA_ROOT   library root, mounted read-write (default /media)
  BACKUP_ROOT  off-library revert/ledger volume (default /revert)
  PORT         listen port (default 8095)
  ALIAS_PAIRS  JSON array of arrays of artist names that are the same act under
               different names; supplied at runtime (names are restricted-tier
               and never committed). Suppresses benign rename mismatches.
  GONIC_URL / GONIC_ADMIN_USER / GONIC_ADMIN_PASS  optional; enable `rescan`.
"""
import datetime
import json
import os
import re
import shutil
import unicodedata
import urllib.parse
import urllib.request
import uuid

import mutagen
from mcp.server.fastmcp import FastMCP

MEDIA_ROOT = os.environ.get("MEDIA_ROOT", "/media").rstrip("/")
BACKUP_ROOT = os.environ.get("BACKUP_ROOT", "/revert").rstrip("/")
PORT = int(os.environ.get("PORT", "8095"))
LEDGER = os.path.join(BACKUP_ROOT, "ledger.jsonl")
AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".opus", ".ogg", ".wav", ".aac"}
_VA_NORMS = {"various", "variousartists", "va", "compilation", "compilations", "ost", "soundtrack"}
_VA_FOLDER_RE = re.compile(r"^\s*(va|various)\b", re.IGNORECASE)

mcp = FastMCP("rick-library", host="0.0.0.0", port=PORT)


# ---------------------------------------------------------------------------
# identity normalization (mirrors the beets-misfile-audit detector)
# ---------------------------------------------------------------------------
def _norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.split(r"\b(feat\.?|ft\.?|featuring|&|,|with|presents|pres\.?|vs\.?|x )\b", s)[0]
    return re.sub(r"[^a-z0-9]", "", s)


def _casenorm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.strip().lower())


def _load_aliases():
    try:
        pairs = json.loads(os.environ.get("ALIAS_PAIRS", "[]"))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for grp in pairs:
        if isinstance(grp, (list, tuple)):
            s = {_norm(x) for x in grp if _norm(x)}
            if len(s) >= 2:
                out.append(s)
    return out


ALIASES = _load_aliases()


def _related(a, b):
    ca, cb = _casenorm(a), _casenorm(b)
    if ca and ca == cb:
        return True
    na, nb = _norm(a), _norm(b)
    return bool(na and nb and (na == nb or na in nb or nb in na))


def _is_va(name):
    return _norm(name) in _VA_NORMS or bool(_VA_FOLDER_RE.match(name or ""))


def _alias_ok(folder, tag):
    if _related(folder, tag):
        return True
    nf, nt = _norm(folder), _norm(tag)
    return any(nf in g and nt in g for g in ALIASES)


# ---------------------------------------------------------------------------
# tag I/O (mutagen; robust to the nonstandard `album_artist` Vorbis key)
# ---------------------------------------------------------------------------
def _audio_files(dirpath):
    try:
        return sorted(f for f in os.listdir(dirpath)
                      if os.path.splitext(f)[1].lower() in AUDIO_EXTS
                      and os.path.isfile(os.path.join(dirpath, f)))
    except OSError:
        return []


def _read_tags(path):
    """Return (artist, album_artist) best-effort, across formats + the underscore key."""
    artist = album_artist = None
    try:
        ez = mutagen.File(path, easy=True)
        if ez is not None and ez.tags:
            if ez.tags.get("artist"):
                artist = str(ez.tags["artist"][0])
            if ez.tags.get("albumartist"):
                album_artist = str(ez.tags["albumartist"][0])
    except Exception:  # noqa: BLE001
        pass
    try:
        raw = mutagen.File(path)
        if raw is not None and raw.tags:
            km = {str(k).lower(): k for k in raw.tags.keys()}
            for cand in ("album_artist", "album artist", "albumartist"):
                if album_artist is None and cand in km:
                    v = raw.tags[km[cand]]
                    v = v[0] if isinstance(v, (list, tuple)) else v
                    album_artist = str(v) if v else album_artist
            if album_artist is None and "TPE2" in raw.tags:
                t = raw.tags["TPE2"]
                vals = getattr(t, "text", None) or [t]
                album_artist = str(vals[0]) if vals else None
            if artist is None:
                for cand in ("artist",):
                    if cand in km:
                        v = raw.tags[km[cand]]
                        v = v[0] if isinstance(v, (list, tuple)) else v
                        artist = str(v) if v else artist
                if artist is None and "TPE1" in raw.tags:
                    t = raw.tags["TPE1"]
                    vals = getattr(t, "text", None) or [t]
                    artist = str(vals[0]) if vals else None
    except Exception:  # noqa: BLE001
        pass
    return artist, album_artist


def _write_album_artist(path, value):
    """Set the album-artist across the keys the player may read. Mutagen edits the
    existing file in place (no unlink); audio is untouched."""
    f = mutagen.File(path)
    if f is None:
        raise RuntimeError("unreadable audio file")
    tags = f.tags
    if tags is None:
        f.add_tags()
        tags = f.tags
    cls = tags.__class__.__name__.lower()
    if "id3" in cls:  # mp3
        from mutagen.id3 import TPE2
        tags.setall("TPE2", [TPE2(encoding=3, text=[value])])
    elif "mp4" in cls:  # m4a
        tags["aART"] = [value]
    else:  # vorbis (flac/ogg/opus) — set both standard and underscore variants
        for k in list(tags.keys()):
            if str(k).lower() in ("albumartist", "album_artist", "album artist"):
                del tags[k]
        tags["albumartist"] = [value]
        tags["album_artist"] = [value]
    f.save()


# ---------------------------------------------------------------------------
# ledger (durable change history on the off-library backup volume)
# ---------------------------------------------------------------------------
def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _append_ledger(entry):
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_ledger():
    if not os.path.exists(LEDGER):
        return []
    out = []
    with open(LEDGER, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def _safe_album(path):
    """Resolve + confine a caller path to MEDIA_ROOT; return (abs, relpath, artist_folder)."""
    ap = os.path.realpath(path if path.startswith("/") else os.path.join(MEDIA_ROOT, path))
    root = os.path.realpath(MEDIA_ROOT)
    if ap != root and not ap.startswith(root + os.sep):
        raise ValueError("path escapes MEDIA_ROOT")
    rel = os.path.relpath(ap, root)
    return ap, rel, rel.split(os.sep)[0]


# ---------------------------------------------------------------------------
# evaluation (library-wide-aware confidence)
# ---------------------------------------------------------------------------
def _assess(album_abs, rel, artist_folder):
    files = _audio_files(album_abs)
    tracks = []
    for fn in files:
        art, aa = _read_tags(os.path.join(album_abs, fn))
        tracks.append({"file": fn, "artist": art, "album_artist": aa})
    aas = {t["album_artist"] for t in tracks if t["album_artist"]}
    arts = {t["artist"] for t in tracks if t["artist"]}
    aa_val = next(iter(aas)) if len(aas) == 1 else None  # single consistent album_artist
    # does every track's ARTIST relate to the containing artist folder?
    folder_matches_artist = bool(arts) and all(_related(a, artist_folder) for a in arts)
    aa_matches_folder = aa_val is not None and _related(aa_val, artist_folder)

    verdict, recommend, reason = "PROPOSE", None, ""
    if not files:
        verdict, reason = "SKIP", "no audio files in directory"
    elif _is_va(artist_folder) or (aa_val and _is_va(aa_val)):
        verdict, reason = "SUPPRESS", "various-artists / compilation bucket"
    elif aa_val is None:
        verdict, reason = "PROPOSE", "album_artist is inconsistent across tracks"
    elif folder_matches_artist and not aa_matches_folder:
        # Content (ARTIST) agrees with the folder on every track; only the
        # album_artist label is the odd one out. Unambiguous corrupt label.
        verdict = "HIGH"
        recommend = {"set_album_artist": _canonical(artist_folder, arts)}
        reason = ("ARTIST tag agrees with the folder on all tracks, only album_artist "
                  "is the odd one out — unambiguous corrupt album_artist")
    elif folder_matches_artist and aa_matches_folder:
        verdict, reason = "SUPPRESS", "folder, ARTIST and album_artist all agree"
    elif aa_matches_folder and not folder_matches_artist:
        # Label + folder agree, but the CONTENT (ARTIST) is a different artist —
        # the original-bug shape (a whole album dumped under the wrong artist and
        # labelled to match the folder). The right fix (move to the real artist,
        # or treat as a cover) is a judgement call, so never auto-apply.
        if not arts:
            verdict, reason = "SUPPRESS", "folder and album_artist agree; no ARTIST signal to contradict"
        else:
            verdict, reason = "PROPOSE", ("album_artist matches the folder but the ARTIST tag is a "
                                          "different artist — likely a cover or a whole-album misfile; "
                                          "needs judgement")
    elif _alias_ok(artist_folder, aa_val):
        verdict, reason = "SUPPRESS", "folder and album_artist are a known alias of the same act"
    else:
        verdict, reason = "PROPOSE", ("neither ARTIST nor album_artist matches the folder — "
                                      "cover, wrong content, or stale folder; needs judgement")
    return {
        "path": rel, "artist_folder": artist_folder, "track_count": len(files),
        "distinct_artist_tags": sorted(arts), "distinct_album_artist_tags": sorted(aas),
        "confidence": verdict, "recommend": recommend, "reason": reason,
    }


def _canonical(artist_folder, _artist_tags):
    """The album-artist value to write for a HIGH fix: the FOLDER name — the clean
    grouping identity the album should sit under, matching how the artist's other
    albums are foldered. Deliberately NOT a track ARTIST: those can be a 'ft. …'
    credit or a short/alias form that relates to the folder but would group the
    album under a one-off string (e.g. 'Daft Punk ft. …' or 'Ye' instead of
    'Daft Punk' / 'Kanye West'). The player groups by this text, so it must be the
    folder's canonical name."""
    return artist_folder


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------
@mcp.tool()
def evaluate(album_path: str) -> dict:
    """Read-only. Assess one album directory (library-wide-aware) and return the
    per-track artist/album_artist signals, a confidence verdict (HIGH / PROPOSE /
    SUPPRESS / SKIP), and the recommended fix. Does not change anything."""
    album_abs, rel, artist_folder = _safe_album(album_path)
    if not os.path.isdir(album_abs):
        return {"error": "not a directory", "path": rel}
    return _assess(album_abs, rel, artist_folder)


@mcp.tool()
def propose(album_path: str) -> dict:
    """Read-only alias of evaluate() — the recommendation without applying it."""
    return evaluate(album_path)


@mcp.tool()
def apply(album_path: str, set_album_artist: str = "", confirmed: bool = False) -> dict:
    """Apply a remediation to one album directory: set every track's album-artist
    to `set_album_artist`. BACKS UP each original file to the off-library volume
    first, records the change to the ledger, and returns a reversible change_id.

    Refuses unless the assessment is HIGH confidence OR `confirmed=True` is passed
    (the approval gate for PROPOSE-tier fixes). Never deletes anything.
    """
    album_abs, rel, artist_folder = _safe_album(album_path)
    if not os.path.isdir(album_abs):
        return {"error": "not a directory", "path": rel}
    assessment = _assess(album_abs, rel, artist_folder)
    target = set_album_artist.strip() or (assessment.get("recommend") or {}).get("set_album_artist", "")
    if not target:
        return {"error": "no album_artist to set and none recommended", "assessment": assessment}
    # Already correct on every track → nothing to do. No backup, no ledger churn.
    if assessment.get("distinct_album_artist_tags") == [target]:
        return {"noop": True, "path": rel, "album_artist": target,
                "reason": "every track already has this album_artist; nothing to change"}
    if assessment["confidence"] != "HIGH" and not confirmed:
        return {"refused": "assessment is not HIGH confidence; pass confirmed=true to override",
                "assessment": assessment}

    # Pre-flight: EVERY file must be writable before we touch ANY. This keeps apply
    # atomic — a mid-album permission failure would otherwise leave the album
    # half-changed with no ledger entry (unrevertable). A file that is not writable
    # is almost always mode 644 owned by another uid; report it for a permission fix
    # rather than corrupting the album.
    files = _audio_files(album_abs)
    unwritable = [fn for fn in files if not os.access(os.path.join(album_abs, fn), os.W_OK)]
    if unwritable:
        return {"blocked": True, "path": rel,
                "reason": (f"{len(unwritable)} of {len(files)} files are not writable "
                           "(likely mode 644 owned by another uid); permission "
                           "normalization needed before this album can be fixed"),
                "unwritable": unwritable[:10]}

    change_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    backup_dir = os.path.join(BACKUP_ROOT, change_id)
    os.makedirs(backup_dir, exist_ok=True)
    changed = []
    for fn in files:
        src = os.path.join(album_abs, fn)
        _old_artist, old_aa = _read_tags(src)
        shutil.copy2(src, os.path.join(backup_dir, fn))  # backup-first (full file)
        _write_album_artist(src, target)
        changed.append({"file": fn, "old_album_artist": old_aa, "new_album_artist": target})

    entry = {
        "change_id": change_id, "ts": _now(), "op": "set_album_artist",
        "path": rel, "artist_folder": artist_folder, "new_album_artist": target,
        "confidence": assessment["confidence"], "reason": assessment["reason"],
        "confirmed": bool(confirmed), "backup_dir": backup_dir,
        "files": changed, "reverted": False,
    }
    _append_ledger(entry)
    return {"applied": True, "change_id": change_id, "files_changed": len(changed),
            "new_album_artist": target, "backup_dir": backup_dir,
            "note": "trigger a player rescan (rescan tool) to surface the change"}


@mcp.tool()
def revert(change_id: str) -> dict:
    """Undo a prior change: restore every backed-up original file from the backup
    volume, overwriting the modified file. Appends a reversal record to the ledger."""
    entry = next((e for e in reversed(_read_ledger()) if e.get("change_id") == change_id), None)
    if not entry:
        return {"error": "unknown change_id", "change_id": change_id}
    backup_dir = entry.get("backup_dir", "")
    album_abs = os.path.realpath(os.path.join(MEDIA_ROOT, entry["path"]))
    restored = []
    for f in entry.get("files", []):
        bak = os.path.join(backup_dir, f["file"])
        dst = os.path.join(album_abs, f["file"])
        if os.path.isfile(bak):
            # copyfile (data only) — NOT copy2: the library file is group-writable
            # but owned by another uid, so copy2's copystat (chmod/utime) would
            # EPERM. The file keeps its own owner/mode; only the bytes are restored.
            shutil.copyfile(bak, dst)
            restored.append(f["file"])
    _append_ledger({"change_id": change_id, "ts": _now(), "op": "revert",
                    "restored": restored, "path": entry["path"]})
    return {"reverted": True, "change_id": change_id, "files_restored": len(restored)}


@mcp.tool()
def history(limit: int = 50, artist_folder: str = "") -> list:
    """Read-only. Return the most recent change-ledger entries (optionally filtered
    to one artist folder). The durable record of everything Rick has changed."""
    rows = _read_ledger()
    if artist_folder:
        rows = [r for r in rows if r.get("artist_folder") == artist_folder]
    return rows[-max(1, min(limit, 500)):]


@mcp.tool()
def rescan() -> dict:
    """Trigger a player (gonic/Subsonic) library rescan so applied changes surface.
    No-op if gonic credentials are not configured."""
    base = os.environ.get("GONIC_URL", "").rstrip("/")
    u, p = os.environ.get("GONIC_ADMIN_USER"), os.environ.get("GONIC_ADMIN_PASS")
    if not (base and u and p):
        return {"skipped": "gonic credentials not configured"}
    q = urllib.parse.urlencode({"u": u, "p": p, "v": "1.16.1", "c": "rick-library", "f": "json"})
    try:
        with urllib.request.urlopen(f"{base}/rest/startScan.view?{q}", timeout=15) as r:
            d = json.loads(r.read().decode())["subsonic-response"]
        return {"status": d.get("status"), "scanning": d.get("scanStatus", {}).get("scanning")}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}


if __name__ == "__main__":
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    mcp.run(transport="streamable-http")
