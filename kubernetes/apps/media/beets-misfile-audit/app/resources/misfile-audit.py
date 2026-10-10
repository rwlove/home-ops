#!/usr/bin/env python3
"""Read-only detector for misfiled single-track directories.

Background: the unattended acquisition pipeline occasionally grabs a
same-titled track by the WRONG artist for a niche artist the source network
barely seeds, and the library manager files it under the INTENDED artist's
folder (right folder, wrong content). It surfaces as a one-track "album" by
the wrong artist in the player.

This job walks the media tree for recently-added single-track directories,
fingerprints each file (AcoustID — authoritative for what the audio actually
is), and reports any whose fingerprinted artist does not match the top-level
artist folder it was filed under. Report-only: it never moves, deletes, tags,
or writes anything. Output goes to stdout (read via `kubectl logs`).

Env:
  MEDIA_ROOT   media tree root (default /media)
  LOOKBACK_DAYS only consider dirs whose single file changed within N days
                (default 8; 0 = scan everything)
  MIN_SCORE    minimum AcoustID score to trust a fingerprint (default 0.90)
"""
import os
import re
import sys
import time

import acoustid

# pyacoustid defaults to http:// (port 80, blocked cluster-wide); force HTTPS.
acoustid.set_base_url("https://api.acoustid.org/v2/")

# beets' embedded AcoustID application key (lookups only).
API_KEY = "1vOwZtEn"

AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".opus", ".ogg", ".wav", ".aac"}
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", "/media")
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "8"))
MIN_SCORE = float(os.environ.get("MIN_SCORE", "0.90"))


def norm(s):
    s = (s or "").lower()
    s = re.split(r"\b(feat\.?|ft\.?|featuring|&|,|with|presents|pres\.?|vs\.?|x )\b", s)[0]
    return re.sub(r"[^a-z0-9]", "", s)


def related(a, b):
    na, nb = norm(a), norm(b)
    return bool(na and nb and (na == nb or na in nb or nb in na))


def audio_files(dirpath):
    return [f for f in os.listdir(dirpath)
            if os.path.splitext(f)[1].lower() in AUDIO_EXTS
            and os.path.isfile(os.path.join(dirpath, f))]


def main():
    cutoff = time.time() - LOOKBACK_DAYS * 86400 if LOOKBACK_DAYS > 0 else 0
    scanned = 0
    mismatches = []
    errors = 0

    for dirpath, dirnames, filenames in os.walk(MEDIA_ROOT):
        rel = os.path.relpath(dirpath, MEDIA_ROOT)
        if rel == ".":
            continue
        parts = rel.split(os.sep)
        artist_folder = parts[0]  # top-level = the intended artist

        auds = audio_files(dirpath)
        if len(auds) != 1:
            continue  # only single-track dirs (the misfile signature)
        path = os.path.join(dirpath, auds[0])
        try:
            if cutoff and os.path.getmtime(path) < cutoff:
                continue
        except OSError:
            continue

        scanned += 1
        try:
            results = sorted(acoustid.match(API_KEY, path),
                             key=lambda x: -(x[0] or 0))
        except Exception as e:  # noqa: BLE001 - report-only, keep going
            errors += 1
            print(f"  ERR id-lookup {rel!r}: {type(e).__name__}: {str(e)[:80]}")
            continue
        time.sleep(0.4)  # AcoustID courtesy rate limit
        if not results:
            continue
        score, _rid, fp_title, fp_artist = results[0]
        if score < MIN_SCORE or not fp_artist:
            continue
        if not related(fp_artist, artist_folder):
            mismatches.append((rel, artist_folder, fp_artist, fp_title, score))

    print(f"[misfile-audit] media={MEDIA_ROOT} lookback_days={LOOKBACK_DAYS} "
          f"min_score={MIN_SCORE} single-track-dirs-scanned={scanned} "
          f"lookup-errors={errors} mismatches={len(mismatches)}")
    for rel, folder, fp_artist, fp_title, score in mismatches:
        print(f"[misfile-audit] MISFILED under {folder!r} but fingerprints as "
              f"{fp_artist!r} — {fp_title!r} (score {score:.2f}) :: {rel}")
    if not mismatches:
        print("[misfile-audit] no misfiled single-track directories found.")
    # Report-only: always succeed so a finding is never a job failure.
    return 0


if __name__ == "__main__":
    sys.exit(main())
