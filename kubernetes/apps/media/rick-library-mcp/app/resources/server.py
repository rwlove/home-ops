#!/usr/bin/env python3
"""rick-library-mcp — bounded, reversible, MusicBrainz-authoritative library
remediation for the kagent `rick` agent (the ward of the library).

Rick fixes misfiled music: an album grouped under the WRONG artist because its
album-artist TEXT tag is corrupt (the player groups by that text), or whole
content sitting in the wrong artist's folder. Identity is decided by
**MusicBrainz** — the authority that knows when two names are the same artist (an
alias or rename) and which artist a recording actually is regardless of the
folder it sits in — not by a hand-maintained list or a pure text guess.

`evaluate()` decides identity from exact MusicBrainz ids — the album's embedded
artist MBID vs. the folder's canonical MBID (derived from that folder's OTHER
albums, i.e. library-authoritative). Fuzzy MB name search is used only as a last
resort for the folder, and only when the top result is an EXACT name match; a
relocate target name is resolved by an exact lookup *by MBID*. It returns an ACTION:
  * retag    — content IS the folder artist (same MBID); album_artist text is
               corrupt → set album_artist to the folder name (via `apply`).
  * relocate — content is a DIFFERENT artist (different MBID) → move the album to
               that artist's folder (via `relocate`).
  * leave    — same entity (alias/rename), compilation, or already correct.
  * skip     — no audio.
When MBIDs can't decide, it degrades to a SAFE text heuristic: retag the clear
corrupt-label case, else leave — it NEVER relocates on a guess.

Every mutation is:
  * BACKED UP FIRST to an off-library volume (BACKUP_ROOT) — fully revertible.
  * RECORDED in a durable JSONL ledger (returned to the caller too).
  * PRE-FLIGHTED for writability so a change can never half-apply.
  * NEVER A DELETE — the only mutations are tag edits and moves, both reversible.

Env:
  MEDIA_ROOT   library root, mounted read-write (default /media)
  BACKUP_ROOT  off-library revert/ledger/mb-cache volume (default /revert)
  PORT         listen port (default 8095)
  GONIC_URL / GONIC_ADMIN_USER / GONIC_ADMIN_PASS  optional; enable `rescan`.
"""
import datetime
import json
import os
import re
import shutil
import time
import unicodedata
import urllib.error
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

# MusicBrainz artist-identity resolution.
MB_API = "https://musicbrainz.org/ws/2/artist"
MB_UA = "rick-library-mcp/1.0 ( https://github.com/rwlove/home-ops )"  # MB requires a UA
MB_CACHE_PATH = os.path.join(BACKUP_ROOT, "mb-cache.json")
_mb_last = [0.0]  # last request time (for the <=1 req/s rate limit)

mcp = FastMCP("rick-library", host="0.0.0.0", port=PORT)


# ---------------------------------------------------------------------------
# identity normalization + text fallback (used only when MusicBrainz is unsure)
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


def _related(a, b):
    ca, cb = _casenorm(a), _casenorm(b)
    if ca and ca == cb:
        return True
    na, nb = _norm(a), _norm(b)
    return bool(na and nb and (na == nb or na in nb or nb in na))


def _is_va(name):
    return _norm(name) in _VA_NORMS or bool(_VA_FOLDER_RE.match(name or ""))


# ---------------------------------------------------------------------------
# MusicBrainz identity (authoritative). Cached + rate-limited; None when unsure.
# ---------------------------------------------------------------------------
def _load_mb_cache():
    try:
        with open(MB_CACHE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


_mb_cache = _load_mb_cache()


def _save_mb_cache():
    try:
        tmp = MB_CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_mb_cache, fh, ensure_ascii=False)
        os.replace(tmp, MB_CACHE_PATH)
    except Exception:  # noqa: BLE001
        pass


def _mb_get(url):
    """Rate-limited (<=1 req/s) MusicBrainz GET -> parsed JSON, None on failure.
    One retry on 503 (MB's busy/limited response)."""
    for attempt in range(2):
        dt = time.time() - _mb_last[0]
        if dt < 1.1:
            time.sleep(1.1 - dt)
        _mb_last[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": MB_UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:  # noqa: PERF203
            if e.code == 503 and attempt == 0:
                time.sleep(2.0)
                continue
            return None
        except Exception:  # noqa: BLE001
            return None
    return None


def _mb_name_to_mbid(name):
    """Resolve an artist NAME to its MusicBrainz MBID, but ONLY when the top result
    is an EXACT name match (accent-folded). MB name search is fuzzy and returns the
    wrong entity for aliases/short names (e.g. an alias resolves to a group the
    artist was in), so a non-exact top match is rejected (None) and never trusted.
    This path never triggers a relocate on its own. Cached + rate-limited."""
    want = _casenorm(name)
    if not want:
        return None
    key = "n:" + want
    if key in _mb_cache:
        return _mb_cache[key]
    mbid = None
    q = urllib.parse.urlencode({"query": 'artist:"%s"' % name, "fmt": "json", "limit": 5})
    d = _mb_get(MB_API + "?" + q)
    for a in (d or {}).get("artists", []) or []:
        if a.get("id") and _casenorm(a.get("name", "")) == want:
            mbid = a["id"]
            break
    _mb_cache[key] = mbid
    _save_mb_cache()
    return mbid


def _mb_mbid_to_name(mbid):
    """Canonical artist name for an MBID — an EXACT lookup by id (not fuzzy search),
    so it is reliable for naming a relocate target. Cached + rate-limited."""
    if not mbid:
        return None
    key = "i:" + mbid
    if key in _mb_cache:
        return _mb_cache[key]
    d = _mb_get("https://musicbrainz.org/ws/2/artist/%s?fmt=json" % urllib.parse.quote(mbid))
    name = (d or {}).get("name")
    _mb_cache[key] = name
    _save_mb_cache()
    return name


def _dominant(mbids):
    """The most common non-empty value in a list (the folder's canonical identity
    from its albums), or None."""
    counts = {}
    for m in mbids:
        if m:
            counts[m] = counts.get(m, 0) + 1
    return max(counts, key=counts.get) if counts else None


def _album_identity_mbid(album_abs):
    """The content (performer) MBID of an album: the dominant embedded artist MBID
    across its tracks, else the dominant album-artist MBID. None if untagged."""
    tracks = [_read_tags(os.path.join(album_abs, fn)) for fn in _audio_files(album_abs)]
    return _dominant([t[2] for t in tracks]) or _dominant([t[3] for t in tracks])


def _folder_mbid(artist_folder, exclude_abs):
    """The folder's canonical artist MBID, derived from its OTHER albums' embedded
    MBIDs (library-authoritative — the user's 'evaluate the whole library'
    principle). None if no sibling album carries an MBID."""
    folder_dir = os.path.join(MEDIA_ROOT, artist_folder)
    exclude_real = os.path.realpath(exclude_abs)
    mbids = []
    try:
        for name in sorted(os.listdir(folder_dir)):
            sub = os.path.join(folder_dir, name)
            if not os.path.isdir(sub) or os.path.realpath(sub) == exclude_real:
                continue
            ident = _album_identity_mbid(sub)
            if ident is None:  # peek one level deeper (multi-disc folders)
                for d2 in sorted(os.listdir(sub)):
                    s2 = os.path.join(sub, d2)
                    if os.path.isdir(s2) and os.path.realpath(s2) != exclude_real:
                        ident = _album_identity_mbid(s2)
                        if ident:
                            break
            if ident:
                mbids.append(ident)
    except OSError:
        pass
    return _dominant(mbids)


# ---------------------------------------------------------------------------
# tag I/O (mutagen; robust to the nonstandard `album_artist` Vorbis key + MBIDs)
# ---------------------------------------------------------------------------
def _audio_files(dirpath):
    try:
        return sorted(f for f in os.listdir(dirpath)
                      if os.path.splitext(f)[1].lower() in AUDIO_EXTS
                      and os.path.isfile(os.path.join(dirpath, f)))
    except OSError:
        return []


def _first(v):
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
    return str(v) if v else None


def _read_tags(path):
    """Return (artist, album_artist, artist_mbid, album_artist_mbid) best-effort,
    across formats, the nonstandard `album_artist` Vorbis key, and the MB id tags."""
    artist = album_artist = artist_mbid = aa_mbid = None
    try:
        ez = mutagen.File(path, easy=True)
        if ez is not None and ez.tags:
            artist = _first(ez.tags.get("artist")) or artist
            album_artist = _first(ez.tags.get("albumartist")) or album_artist
            artist_mbid = _first(ez.tags.get("musicbrainz_artistid")) or artist_mbid
            aa_mbid = _first(ez.tags.get("musicbrainz_albumartistid")) or aa_mbid
    except Exception:  # noqa: BLE001
        pass
    try:
        raw = mutagen.File(path)
        if raw is not None and raw.tags:
            km = {str(k).lower(): k for k in raw.tags.keys()}

            def _rawget(*cands):
                for c in cands:
                    if c in km:
                        return _first(raw.tags[km[c]])
                return None

            if album_artist is None:
                album_artist = _rawget("album_artist", "album artist", "albumartist")
                if album_artist is None and "TPE2" in raw.tags:
                    album_artist = _first(getattr(raw.tags["TPE2"], "text", None) or raw.tags["TPE2"])
            if artist is None:
                artist = _rawget("artist")
                if artist is None and "TPE1" in raw.tags:
                    artist = _first(getattr(raw.tags["TPE1"], "text", None) or raw.tags["TPE1"])
            if artist_mbid is None:
                artist_mbid = _rawget("musicbrainz_artistid", "musicbrainz artist id")
                if artist_mbid is None and "TXXX:MusicBrainz Artist Id" in raw.tags:
                    artist_mbid = _first(getattr(raw.tags["TXXX:MusicBrainz Artist Id"], "text", None))
            if aa_mbid is None:
                aa_mbid = _rawget("musicbrainz_albumartistid", "musicbrainz album artist id")
                if aa_mbid is None and "TXXX:MusicBrainz Album Artist Id" in raw.tags:
                    aa_mbid = _first(getattr(raw.tags["TXXX:MusicBrainz Album Artist Id"], "text", None))
    except Exception:  # noqa: BLE001
        pass
    # MB ids can be multi-valued (collabs) as "id1;id2" — keep the first.
    artist_mbid = (artist_mbid or "").split(";")[0].strip() or None
    aa_mbid = (aa_mbid or "").split(";")[0].strip() or None
    return artist, album_artist, artist_mbid, aa_mbid


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


def _change_id():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]


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


def _unwritable(album_abs, files):
    return [fn for fn in files if not os.access(os.path.join(album_abs, fn), os.W_OK)]


# ---------------------------------------------------------------------------
# evaluation (MusicBrainz-authoritative; text heuristic only as a safe fallback)
# ---------------------------------------------------------------------------
def _assess(album_abs, rel, artist_folder):
    files = _audio_files(album_abs)
    base = {"path": rel, "artist_folder": artist_folder, "track_count": len(files)}
    if not files:
        return {**base, "action": "skip", "confidence": "SKIP", "reason": "no audio files in directory"}
    if _is_va(artist_folder):
        return {**base, "action": "leave", "confidence": "SUPPRESS", "reason": "various-artists / compilation bucket"}

    tracks = [_read_tags(os.path.join(album_abs, fn)) for fn in files]
    arts = {t[0] for t in tracks if t[0]}
    aas = {t[1] for t in tracks if t[1]}
    aa_val = next(iter(aas)) if len(aas) == 1 else None
    art_val = next(iter(arts)) if len(arts) == 1 else None
    base.update({"distinct_artist_tags": sorted(arts), "distinct_album_artist_tags": sorted(aas)})

    if aa_val and _is_va(aa_val):
        return {**base, "action": "leave", "confidence": "SUPPRESS", "reason": "compilation album_artist"}

    # text signals (used only when MBIDs can't decide)
    folder_matches_artist = bool(arts) and all(_related(a, artist_folder) for a in arts)
    aa_matches_folder = aa_val is not None and _related(aa_val, artist_folder)

    # --- RELIABLE identity: embedded MBIDs (exact), folder MBID from library ---
    content_mbid = _dominant([t[2] for t in tracks]) or _dominant([t[3] for t in tracks])
    # folder identity: derived from sibling albums first (exact, library-authoritative),
    # else an EXACT-name-match MB lookup (fuzzy matches are rejected, never trusted).
    folder_mbid = _folder_mbid(artist_folder, album_abs) or _mb_name_to_mbid(artist_folder)
    base["mb"] = {"folder_mbid": folder_mbid, "content_mbid": content_mbid}

    if folder_mbid and content_mbid:
        if folder_mbid == content_mbid:
            # Content IS the folder artist (MBID-confirmed), so album_artist SHOULD be
            # the folder name. Leave only if it already is (single value matching the
            # folder); otherwise retag — whether the current album_artist is a single
            # wrong value OR inconsistent across tracks (both are corruption).
            if aa_val is not None and _related(aa_val, artist_folder):
                return {**base, "action": "leave", "confidence": "SUPPRESS",
                        "reason": "content, folder and album_artist agree (MBID-confirmed)"}
            return {**base, "action": "retag", "target": artist_folder, "confidence": "HIGH",
                    "recommend": {"set_album_artist": artist_folder},
                    "reason": "MBID says the content is the folder artist; album_artist is corrupt -> retag to folder"}
        # Content is a DIFFERENT artist by MBID -> wrong content -> relocate, but only
        # with a reliably-resolved target name (exact MBID lookup, else the ARTIST tag).
        target = _mb_mbid_to_name(content_mbid) or art_val
        if target and not _related(target, artist_folder):
            return {**base, "action": "relocate", "target": target, "confidence": "HIGH",
                    "reason": "content MBID differs from the folder's MBID; relocate to %s" % target}
        return {**base, "action": "leave", "confidence": "PROPOSE",
                "reason": "content differs from folder by MBID but the target name could not be resolved"}

    # --- MBIDs can't decide -> SAFE text fallback: retag the clear corrupt-label
    #     case only; otherwise leave untouched. NEVER relocate on a text guess. ---
    if folder_matches_artist and not aa_matches_folder and aa_val is not None:
        return {**base, "action": "retag", "target": artist_folder, "confidence": "HIGH",
                "recommend": {"set_album_artist": artist_folder},
                "reason": "text fallback: ARTIST agrees with folder, album_artist is the odd one -> retag"}
    return {**base, "action": "leave", "confidence": "PROPOSE",
            "reason": "could not reliably identify (no usable MBID, no clear text signal); left unchanged"}


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------
@mcp.tool()
def evaluate(album_path: str) -> dict:
    """Read-only. Assess one album directory using MusicBrainz as the identity
    authority and return the per-track signals, the resolved MB ids, and a
    recommended ACTION: retag (corrupt album_artist -> folder), relocate (content
    is a different MB artist -> move to that artist), leave (alias/rename/already
    correct), or skip. Changes nothing."""
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
    """Retag one album's album-artist (for the `retag` action: content is the folder
    artist but album_artist is corrupt). Backs up each file first, records a
    reversible change_id. Refuses unless the assessment is HIGH confidence OR
    confirmed=True. Never deletes, never moves — use `relocate` to move content."""
    album_abs, rel, artist_folder = _safe_album(album_path)
    if not os.path.isdir(album_abs):
        return {"error": "not a directory", "path": rel}
    assessment = _assess(album_abs, rel, artist_folder)
    target = set_album_artist.strip() or (assessment.get("recommend") or {}).get("set_album_artist", "")
    if not target:
        return {"error": "no album_artist to set and none recommended", "assessment": assessment}
    if assessment.get("distinct_album_artist_tags") == [target]:
        return {"noop": True, "path": rel, "album_artist": target,
                "reason": "every track already has this album_artist; nothing to change"}
    if assessment["confidence"] != "HIGH" and not confirmed:
        return {"refused": "assessment is not HIGH confidence; pass confirmed=true to override",
                "assessment": assessment}

    files = _audio_files(album_abs)
    unwritable = _unwritable(album_abs, files)
    if unwritable:
        return {"blocked": True, "path": rel,
                "reason": ("%d of %d files are not writable (likely mode 644 owned by another "
                           "uid); permission normalization needed" % (len(unwritable), len(files))),
                "unwritable": unwritable[:10]}

    change_id = _change_id()
    backup_dir = os.path.join(BACKUP_ROOT, change_id)
    os.makedirs(backup_dir, exist_ok=True)
    changed = []
    for fn in files:
        src = os.path.join(album_abs, fn)
        _artist, old_aa, _am, _aam = _read_tags(src)
        shutil.copy2(src, os.path.join(backup_dir, fn))  # backup-first (full file)
        _write_album_artist(src, target)
        changed.append({"file": fn, "old_album_artist": old_aa, "new_album_artist": target})

    _append_ledger({
        "change_id": change_id, "ts": _now(), "op": "set_album_artist",
        "path": rel, "artist_folder": artist_folder, "new_album_artist": target,
        "confidence": assessment["confidence"], "reason": assessment["reason"],
        "confirmed": bool(confirmed), "backup_dir": backup_dir, "files": changed, "reverted": False,
    })
    return {"applied": True, "change_id": change_id, "files_changed": len(changed),
            "new_album_artist": target, "backup_dir": backup_dir,
            "note": "call rescan to surface the change in the player"}


@mcp.tool()
def relocate(album_path: str, to_artist: str = "", confirmed: bool = False) -> dict:
    """Move a misfiled album to the correct artist's folder (the `relocate` action:
    the content is a different artist than its current folder) and fix its
    album_artist. Backs up every file first, moves the album dir under
    /media/<to_artist>/, records a reversible change_id. Refuses unless the
    assessment recommends a relocate OR confirmed=True. Never deletes; never
    overwrites an existing target."""
    album_abs, rel, artist_folder = _safe_album(album_path)
    if not os.path.isdir(album_abs):
        return {"error": "not a directory", "path": rel}
    assessment = _assess(album_abs, rel, artist_folder)
    target_artist = to_artist.strip() or assessment.get("target", "")
    if not target_artist:
        return {"error": "no target artist and none recommended", "assessment": assessment}
    if assessment.get("action") != "relocate" and not confirmed:
        return {"refused": "assessment does not recommend a relocate; pass confirmed=true to override",
                "assessment": assessment}

    safe_artist = target_artist.replace("/", "_").strip().strip(".") or target_artist
    basename = os.path.basename(album_abs.rstrip("/"))
    new_artist_dir = os.path.join(MEDIA_ROOT, safe_artist)
    new_abs = os.path.join(new_artist_dir, basename)
    if os.path.realpath(new_abs) == os.path.realpath(album_abs):
        return {"noop": True, "path": rel, "reason": "already under the target artist"}
    if os.path.exists(new_abs):
        return {"error": "target already exists; not overwriting",
                "target": os.path.relpath(new_abs, MEDIA_ROOT)}

    files = _audio_files(album_abs)
    unwritable = _unwritable(album_abs, files)
    if unwritable:
        return {"blocked": True, "path": rel,
                "reason": ("%d of %d files not writable; permission normalization needed"
                           % (len(unwritable), len(files))),
                "unwritable": unwritable[:10]}

    change_id = _change_id()
    backup_dir = os.path.join(BACKUP_ROOT, change_id)
    os.makedirs(backup_dir, exist_ok=True)
    changed = []
    for fn in files:
        src = os.path.join(album_abs, fn)
        _artist, old_aa, _am, _aam = _read_tags(src)
        shutil.copy2(src, os.path.join(backup_dir, fn))  # backup-first
        changed.append({"file": fn, "old_album_artist": old_aa})

    os.makedirs(new_artist_dir, exist_ok=True)
    shutil.move(album_abs, new_abs)  # same NFS fs -> atomic rename
    for fn in files:
        try:
            _write_album_artist(os.path.join(new_abs, fn), target_artist)
        except Exception:  # noqa: BLE001 - tag fix is best-effort; the move already happened
            pass

    new_rel = os.path.relpath(new_abs, MEDIA_ROOT)
    _append_ledger({
        "change_id": change_id, "ts": _now(), "op": "relocate",
        "path": rel, "new_path": new_rel, "artist_folder": artist_folder,
        "new_artist": target_artist, "backup_dir": backup_dir, "files": changed, "reverted": False,
    })
    return {"relocated": True, "change_id": change_id, "from": rel, "to": new_rel,
            "new_album_artist": target_artist, "files_moved": len(files),
            "note": "call rescan to surface the move in the player"}


@mcp.tool()
def revert(change_id: str) -> dict:
    """Undo a prior change. For a retag: restore the original files from backup.
    For a relocate: move the album back to its original folder and restore the
    original files. Appends a reversal record to the ledger."""
    entry = next((e for e in reversed(_read_ledger())
                  if e.get("change_id") == change_id and e.get("op") != "revert"), None)
    if not entry:
        return {"error": "unknown change_id", "change_id": change_id}
    backup_dir = entry.get("backup_dir", "")

    if entry.get("op") == "relocate":
        new_abs = os.path.realpath(os.path.join(MEDIA_ROOT, entry["new_path"]))
        old_abs = os.path.realpath(os.path.join(MEDIA_ROOT, entry["path"]))
        moved = False
        if os.path.isdir(new_abs) and not os.path.exists(old_abs):
            os.makedirs(os.path.dirname(old_abs), exist_ok=True)
            shutil.move(new_abs, old_abs)
            moved = True
        restored = []
        for f in entry.get("files", []):
            bak = os.path.join(backup_dir, f["file"])
            dst = os.path.join(old_abs, f["file"])
            if os.path.isfile(bak) and os.path.isfile(dst):
                shutil.copyfile(bak, dst)  # data only (not owner) — restores original tags
                restored.append(f["file"])
        _append_ledger({"change_id": change_id, "ts": _now(), "op": "revert",
                        "restored": restored, "moved_back": moved, "path": entry["path"]})
        return {"reverted": True, "change_id": change_id, "moved_back": moved,
                "files_restored": len(restored)}

    # set_album_artist (retag)
    album_abs = os.path.realpath(os.path.join(MEDIA_ROOT, entry["path"]))
    restored = []
    for f in entry.get("files", []):
        bak = os.path.join(backup_dir, f["file"])
        dst = os.path.join(album_abs, f["file"])
        if os.path.isfile(bak):
            shutil.copyfile(bak, dst)  # data only; keeps the file's own owner/mode
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
