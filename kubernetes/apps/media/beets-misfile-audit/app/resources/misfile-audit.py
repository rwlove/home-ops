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
    any that does not relate to the top-level artist folder (after skipping
    various-artists compilations, the one benign case decidable offline).

This is a DETECTOR only — it never moves, deletes, or tags. Both passes'
findings are a candidate list handed to the rick ward agent (A2A), which
decides identity by MusicBrainz and resolves each one autonomously (retag /
relocate / leave) — so an artist rename/alias is recognized as the SAME
entity by fact, not suppressed by a hand-maintained list. When REMEDIATE is
off the findings are emailed as a report-only digest via notify-mcp /send
(ALL CLEAR -> nothing sent); either way they are printed to stdout.

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
# Weekly remediation handoff: pass the candidate dirs to the rick ward agent
# (A2A), which MusicBrainz-resolves and fixes each one autonomously (retag /
# relocate / leave) with no human gate. Artist names stay runtime-only — the
# folder list is built at call time; this template carries none. When REMEDIATE
# is on, Rick's action summary is the single report (the audit skips its own
# digest email to avoid a duplicate).
REMEDIATE = os.environ.get("REMEDIATE", "true").lower() in ("1", "true", "yes")
REMEDIATE_AGENT_URL = os.environ.get("REMEDIATE_AGENT_URL",
                                     "http://rick.ai.svc.cluster.local:8080/")

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
        # Offline, only the trivially-benign cases are suppressed here (identical
        # or substring names). Genuine aliases/renames (two different names, one MB
        # entity) are NOT guessed at offline — they're flagged and handed to the
        # ward agent, which recognizes them as the same entity by MusicBrainz and
        # leaves them. No hand-maintained alias list.
        if not related(artist_folder, aa):
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


def remediate(content, album):
    """Hand the candidate directories (both passes) to the rick ward agent (A2A)
    to resolve AUTONOMOUSLY — MusicBrainz decides identity, the agent retags /
    relocates / leaves each one with no human gate, and rescans the player. This
    closes the weekly loop: detect -> resolve -> surface. Best-effort — the audit
    is a detector and NEVER fails on a handoff error (the agent keeps working
    in-cluster even past a client-side timeout)."""
    if not (REMEDIATE and REMEDIATE_AGENT_URL):
        return
    # Union the two passes' directories (a dir can trip both); one line each.
    dirs = []
    seen = set()
    for rel in [c[0] for c in content] + [a[0] for a in album]:
        if rel not in seen:
            seen.add(rel)
            dirs.append(rel)
    if not dirs:
        return
    listing = "\n".join(f"- {rel}" for rel in dirs)
    prompt = (
        "Weekly library-ward sweep. You are the ward of the library. Using "
        "rick-library-mcp, `evaluate` each directory below (MusicBrainz is the "
        "identity authority) and AUTONOMOUSLY carry out the action it returns — "
        "`apply` a retag, `relocate` a wrong-content album, or leave an "
        "alias/rename/correct one. Resolve every item yourself: there is NO "
        "PROPOSE list and NO approval step. This is unattended — never ask for "
        "confirmation and never pass confirmed=true. Every write is backed up and "
        "reversible; that is the safety net, not a human. After making any "
        "change(s), call `rescan` once so the player updates.\n\n"
        "Then notify Rob (notify_rob) with a summary of what you DID — each album "
        "retagged (its new album_artist, from the result's change_id), each "
        "relocated (to which artist), and anything left and why. REPORT "
        "ACCURATELY: an item you just fixed is NOT a no-op — never describe an "
        "applied change as 'already correct' or 'no changes needed'.\n\n"
        "Directories:\n" + listing
    )
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "message/send",
        "params": {"message": {
            "role": "user",
            "parts": [{"kind": "text", "text": prompt}],
            "messageId": "misfile-audit-remediate", "kind": "message",
        }},
    }).encode()
    try:
        req = urllib.request.Request(REMEDIATE_AGENT_URL, data=body, headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        })
        with urllib.request.urlopen(req, timeout=900) as r:
            r.read()
        print(f"[misfile-audit] handed {len(dirs)} candidate dir(s) to the ward "
              "agent for autonomous resolution")
    except Exception as e:  # noqa: BLE001 - never fail the audit on handoff error
        # A client-side timeout is non-fatal: the agent keeps working in-cluster.
        print(f"[misfile-audit] remediation handoff returned: {type(e).__name__}: {str(e)[:120]}")


def main():
    content, cstats = scan_single_track_content()
    album, astats = scan_album_tag()

    print(f"[misfile-audit] media={MEDIA_ROOT} "
          f"single-track(lookback={LOOKBACK_DAYS},min_score={MIN_SCORE}): "
          f"scanned={cstats['scanned']} errors={cstats['errors']} "
          f"misfiled={len(content)} | "
          f"album-tag(lookback={ALBUM_LOOKBACK_DAYS}): "
          f"scanned={astats['scanned']} mismatches={len(album)} | "
          f"remediate={REMEDIATE}")
    for rel, folder, fp_artist, fp_title, score in content:
        print(f"[misfile-audit] CONTENT misfiled under {folder!r} but fingerprints "
              f"as {fp_artist!r} — {fp_title!r} (score {score:.2f}) :: {rel}")
    for rel, folder, aa in album:
        print(f"[misfile-audit] ALBUM-TAG under {folder!r} but album-artist tag is "
              f"{aa!r} :: {rel}")

    if not (content or album):
        print("[misfile-audit] no misfiles found (both passes clear).")
    elif REMEDIATE:
        # The ward agent resolves the findings and emails Rob the single action
        # summary — skip the audit's own digest so Rob gets ONE report, not two.
        print("[misfile-audit] findings handed to the ward agent; audit digest "
              "email suppressed (REMEDIATE on).")
    else:
        n = len(content) + len(album)
        notify(f"Music misfile audit: {n} finding(s)", _digest(content, cstats, album, astats))

    # Close the loop: hand every candidate dir (both passes) to the ward agent to
    # MusicBrainz-resolve autonomously (retag / relocate / leave). No-op when
    # REMEDIATE is off.
    remediate(content, album)
    # Detector only: always succeed so a finding is never a job failure.
    return 0


if __name__ == "__main__":
    sys.exit(main())
