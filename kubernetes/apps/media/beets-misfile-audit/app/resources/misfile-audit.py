#!/usr/bin/env python3
"""Read-only detector for misfiled music directories.

Two independent passes, both report-only — the job never moves, deletes,
tags, or writes anything to the library:

  PASS 1 — single-track content misfile (AcoustID).
    The unattended acquisition pipeline occasionally grabs a same-titled
    track by the WRONG artist for a niche artist the source network barely
    seeds, and the library manager files it under the INTENDED artist's
    folder (right folder, wrong audio). This pass walks recently-added
    single-track dirs, fingerprints each file (AcoustID — authoritative for
    what the audio actually IS), and flags any whose fingerprinted artist
    does not match the top-level artist folder.

  PASS 2 — album-level tag misgroup (offline, mutagen).
    A whole album can land in the wrong artist folder carrying an
    album-artist tag for a DIFFERENT artist (e.g. a 15-track album filed
    under the wrong artist). The player groups by album-artist identity, so
    the album then shows under the wrong artist. Pass 1 misses this because
    it only looks at single-track dirs. This pass reads the embedded
    album-artist of ONE representative track per album directory and flags
    any that does not relate to the top-level artist folder — after
    suppressing known artist aliases/renames (ALIAS_PAIRS, below) and
    various-artists compilations (the benign cases that are not misfiles).

Findings from both passes are emailed as one digest via notify-mcp /send
(ALL CLEAR -> nothing sent) and also printed to stdout (kubectl logs).

Env:
  MEDIA_ROOT           media tree root (default /media)
  LOOKBACK_DAYS        pass 1: only dirs whose single file changed within N
                       days (default 8; 0 = everything)
  MIN_SCORE            pass 1: minimum AcoustID score to trust a fingerprint
                       (default 0.90)
  ALBUM_LOOKBACK_DAYS  pass 2: only album dirs whose representative track
                       changed within N days (default 0 = scan everything;
                       set >0 once the existing backlog is cleared to keep
                       weekly runs light and focused on new imports)
  ALIAS_PAIRS          pass 2 noise control: JSON array of arrays of artist
                       names that are the SAME act under different names, e.g.
                       [["name a","name b"],["name c","name d"]]. A folder-vs-tag
                       mismatch whose two sides fall in one group is benign and
                       suppressed. Supplied at RUNTIME (from a secret / 1Password,
                       never committed) because artist names are restricted-tier
                       and must not live in this public repo (HOMELAB-SPEC L2 #10).
                       Unset -> no alias suppression (benign renames show as
                       findings until the list is populated).
  NOTIFY_URL           notify-mcp /send endpoint (default the in-cluster svc);
                       empty disables the email (stdout only)
  AGENT_NAME           notify-mcp sender/theme (default "rick")
"""
import json
import os
import re
import sys
import time
import unicodedata
import urllib.request

import acoustid
import mutagen

# pyacoustid defaults to http:// (port 80, blocked cluster-wide); force HTTPS.
acoustid.set_base_url("https://api.acoustid.org/v2/")

# beets' embedded AcoustID application key (lookups only).
API_KEY = "1vOwZtEn"

AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".opus", ".ogg", ".wav", ".aac"}
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", "/media")
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "8"))
MIN_SCORE = float(os.environ.get("MIN_SCORE", "0.90"))
ALBUM_LOOKBACK_DAYS = int(os.environ.get("ALBUM_LOOKBACK_DAYS", "0"))
NOTIFY_URL = os.environ.get("NOTIFY_URL",
                            "http://notify-mcp.mcp-system.svc.cluster.local:8060/send")
AGENT_NAME = os.environ.get("AGENT_NAME", "rick")

# Various-artists / compilation markers — pass 2 skips a dir whose artist folder
# or album-artist tag is a VA bucket (per-track artists differ by design). These
# are generic category words, not media content, so they live in code.
_VA_NORMS = {"various", "variousartists", "va", "compilation", "compilations", "ost", "soundtrack"}
_VA_FOLDER_RE = re.compile(r"^\s*(va|various)\b", re.IGNORECASE)


def norm(s):
    """Lowercase, strip accents, drop a trailing collaborator clause, keep alnum."""
    s = (s or "")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.split(r"\b(feat\.?|ft\.?|featuring|&|,|with|presents|pres\.?|vs\.?|x )\b", s)[0]
    return re.sub(r"[^a-z0-9]", "", s)


# Alias allowlist, built from the ALIAS_PAIRS env (artist names kept out of git
# per HOMELAB-SPEC L2 #10). Each group is a set of normalised names that are the
# same act; a folder-vs-tag mismatch within one group is benign.
def _load_aliases():
    try:
        pairs = json.loads(os.environ.get("ALIAS_PAIRS", "[]"))
    except Exception:  # noqa: BLE001 - bad env must not crash the audit
        return []
    out = []
    for grp in pairs:
        if isinstance(grp, (list, tuple)):
            s = {norm(x) for x in grp if norm(x)}
            if len(s) >= 2:
                out.append(s)
    return out


ALIASES = _load_aliases()


def _casenorm(s):
    """Accent-stripped, whitespace-collapsed, lowercased — but KEEPS non-Latin
    letters and symbols. norm() reduces to [a-z0-9] and empties out all-symbol
    or non-Latin names (e.g. a symbol-only or Korean artist name), which made
    related() wrongly flag a folder whose album-artist tag is identical."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.strip().lower())


def related(a, b):
    """True if two names are the same (including non-Latin/symbol names that
    norm() would reduce to empty) or one's alnum form is a substring of the
    other's."""
    ca, cb = _casenorm(a), _casenorm(b)
    if ca and ca == cb:
        return True
    na, nb = norm(a), norm(b)
    return bool(na and nb and (na == nb or na in nb or nb in na))


def is_va(name):
    n = norm(name)
    return n in _VA_NORMS or bool(_VA_FOLDER_RE.match(name or ""))


def alias_ok(folder, tag):
    """True if folder vs tag is benign: related, or a known alias group."""
    if related(folder, tag):
        return True
    nf, nt = norm(folder), norm(tag)
    return any(nf in grp and nt in grp for grp in ALIASES)


def audio_files(dirpath):
    try:
        return [f for f in os.listdir(dirpath)
                if os.path.splitext(f)[1].lower() in AUDIO_EXTS
                and os.path.isfile(os.path.join(dirpath, f))]
    except OSError:
        return []


def read_album_artist(path):
    """Embedded album-artist, robust across formats and the nonstandard
    `album_artist` Vorbis key that the player groups on. None if absent."""
    try:
        ez = mutagen.File(path, easy=True)
        if ez is not None and ez.tags:
            v = ez.tags.get("albumartist")
            if v:
                return str(v[0])
    except Exception:  # noqa: BLE001 - report-only, keep going
        pass
    try:
        raw = mutagen.File(path)
        if raw is not None and raw.tags:
            km = {str(k).lower(): k for k in raw.tags.keys()}
            for cand in ("album_artist", "album artist", "albumartist"):
                if cand in km:
                    v = raw.tags[km[cand]]
                    v = v[0] if isinstance(v, (list, tuple)) else v
                    if v:
                        return str(v)
            if "TPE2" in raw.tags:  # ID3 album-artist frame
                t = raw.tags["TPE2"]
                vals = getattr(t, "text", None) or [t]
                if vals:
                    return str(vals[0])
    except Exception:  # noqa: BLE001
        pass
    return None


def scan_single_track_content():
    """PASS 1 — AcoustID content misfile in single-track dirs. Returns findings
    as (rel, folder, fp_artist, fp_title, score) and a counters dict."""
    cutoff = time.time() - LOOKBACK_DAYS * 86400 if LOOKBACK_DAYS > 0 else 0
    scanned = 0
    errors = 0
    out = []
    for dirpath, _dirs, _files in os.walk(MEDIA_ROOT):
        rel = os.path.relpath(dirpath, MEDIA_ROOT)
        if rel == ".":
            continue
        artist_folder = rel.split(os.sep)[0]
        auds = audio_files(dirpath)
        if len(auds) != 1:
            continue  # only single-track dirs (the content-misfile signature)
        path = os.path.join(dirpath, auds[0])
        try:
            if cutoff and os.path.getmtime(path) < cutoff:
                continue
        except OSError:
            continue
        scanned += 1
        try:
            results = sorted(acoustid.match(API_KEY, path), key=lambda x: -(x[0] or 0))
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"  ERR id-lookup {rel!r}: {type(e).__name__}: {str(e)[:80]}")
            continue
        time.sleep(0.4)  # AcoustID courtesy rate limit
        if not results:
            continue
        score, _rid, _title, fp_artist = results[0]
        if score < MIN_SCORE or not fp_artist:
            continue
        if not related(fp_artist, artist_folder):
            out.append((rel, artist_folder, fp_artist, results[0][2], score))
    return out, {"scanned": scanned, "errors": errors}


def scan_album_tag():
    """PASS 2 — album-level album-artist-vs-folder tag misgroup (offline).
    Returns findings as (rel, folder, album_artist) and a counters dict."""
    cutoff = time.time() - ALBUM_LOOKBACK_DAYS * 86400 if ALBUM_LOOKBACK_DAYS > 0 else 0
    scanned = 0
    out = []
    for dirpath, _dirs, _files in os.walk(MEDIA_ROOT):
        rel = os.path.relpath(dirpath, MEDIA_ROOT)
        if rel == ".":
            continue
        artist_folder = rel.split(os.sep)[0]
        if is_va(artist_folder):
            continue
        auds = sorted(audio_files(dirpath))
        if not auds:
            continue  # not an album dir (no audio directly here)
        rep = os.path.join(dirpath, auds[0])  # one representative track
        try:
            if cutoff and os.path.getmtime(rep) < cutoff:
                continue
        except OSError:
            continue
        scanned += 1
        aa = read_album_artist(rep)
        if not aa or is_va(aa):
            continue
        if not alias_ok(artist_folder, aa):
            out.append((rel, artist_folder, aa))
    return out, {"scanned": scanned}


def _digest(content, cstats, album, astats):
    lines = [
        "FYI: music library misfile audit",
        "",
        (f"Single-track content check: {cstats['scanned']} dirs scanned, "
         f"{cstats['errors']} lookup errors, {len(content)} misfiled."),
        (f"Album tag check: {astats['scanned']} album dirs scanned, "
         f"{len(album)} folder-vs-tag mismatches."),
    ]
    if content:
        lines += ["", "**Content misfiles (audio fingerprint != folder):**"]
        for rel, folder, fp_artist, fp_title, score in content:
            lines.append(f"- under `{folder}` but is **{fp_artist}** — "
                         f"{fp_title} ({score:.2f}) :: `{rel}`")
    if album:
        lines += ["", "**Album tag mismatches (album-artist tag != folder):**"]
        for rel, folder, aa in album:
            lines.append(f"- folder `{folder}` vs album-artist **{aa}** :: `{rel}`")
    lines += ["", "Report-only — nothing was changed. Review and fix deliberately.",
              "This is a one-way notification; no reply is possible."]
    return "\n".join(lines)


def notify(subject, body):
    if not NOTIFY_URL:
        return
    try:
        data = json.dumps({"subject": subject, "body": body, "agent": AGENT_NAME}).encode()
        req = urllib.request.Request(NOTIFY_URL, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            if r.status // 100 != 2:
                raise RuntimeError(f"notify-mcp HTTP {r.status}")
        print("[misfile-audit] digest emailed")
    except Exception as e:  # noqa: BLE001 - never fail the job on a notify error
        print(f"[misfile-audit] notify failed: {type(e).__name__}: {str(e)[:120]}")


def main():
    content, cstats = scan_single_track_content()
    album, astats = scan_album_tag()

    print(f"[misfile-audit] media={MEDIA_ROOT} "
          f"single-track(lookback={LOOKBACK_DAYS},min_score={MIN_SCORE}): "
          f"scanned={cstats['scanned']} errors={cstats['errors']} "
          f"misfiled={len(content)} | "
          f"album-tag(lookback={ALBUM_LOOKBACK_DAYS},aliases={len(ALIASES)}): "
          f"scanned={astats['scanned']} mismatches={len(album)}")
    for rel, folder, fp_artist, fp_title, score in content:
        print(f"[misfile-audit] CONTENT misfiled under {folder!r} but fingerprints "
              f"as {fp_artist!r} — {fp_title!r} (score {score:.2f}) :: {rel}")
    for rel, folder, aa in album:
        print(f"[misfile-audit] ALBUM-TAG under {folder!r} but album-artist tag is "
              f"{aa!r} :: {rel}")

    if content or album:
        n = len(content) + len(album)
        notify(f"Music misfile audit: {n} finding(s)", _digest(content, cstats, album, astats))
    else:
        print("[misfile-audit] no misfiles found (both passes clear).")
    # Report-only: always succeed so a finding is never a job failure.
    return 0


if __name__ == "__main__":
    sys.exit(main())
