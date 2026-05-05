#!/usr/bin/env python3
"""
Bridge soularr's failed_imports through Lidarr's manualImport API.

Soularr triggers Lidarr's DownloadedAlbumsScan after each download. When
Lidarr's matcher rejects (album-match score < 80% — typical for mixtapes,
compilations, scene releases), soularr moves the dir to
/slskd/complete/failed_imports/ and gives up.

This script periodically processes those rejected dirs by:
  1. Calling Lidarr's manualImport endpoint to identify artist/album/tracks
     from the file tags (Lidarr returns proposed matches even though it
     rejected the auto-import).
  2. Creating an album folder under /media/<artist>/<album>/ and moving
     the files there (so Lidarr's import lands them in a tidy structure
     instead of flat under the artist root).
  3. POST-ing a ManualImport command with explicit albumId/artistId/
     trackIds — bypassing the match-score gate that auto-import enforces.

Result: Lidarr registers the files at their final /media path, soularr's
failed_imports queue drains, source dir is removed.
"""
import json
import os
import shutil
import sys
import urllib.parse
import urllib.request
from collections import Counter

LIDARR_URL = os.environ.get("LIDARR_URL", "http://lidarr.media.svc.cluster.local:8686")
LIDARR_API_KEY = os.environ["LIDARR__API_KEY"]
FAILED_IMPORTS_DIR = os.environ.get("FAILED_IMPORTS_DIR", "/slskd/complete/failed_imports")
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", "/media")


def call_api(method, path, params=None, body=None):
    url = f"{LIDARR_URL}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"X-Api-Key": LIDARR_API_KEY}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def sanitize(name):
    """Strip filesystem-hostile characters from a single path component."""
    bad = '/\0'
    out = "".join("_" if c in bad else c for c in name).strip().rstrip(".")
    return out or "unknown"


def process_dir(src_dir):
    print(f"\n=== {src_dir}", flush=True)

    proposals = call_api(
        "GET",
        "/api/v1/manualimport",
        params={"folder": src_dir, "filterExistingFiles": "false"},
    )
    music = [p for p in proposals if p.get("album") and p.get("artist") and p.get("tracks")]
    if not music:
        print("  no identifiable music; skipping", flush=True)
        return

    artist_ids = Counter(p["artist"]["id"] for p in music)
    album_ids = Counter(p["album"]["id"] for p in music)
    if len(artist_ids) > 1 or len(album_ids) > 1:
        print(f"  WARN: mixed artists/albums ({len(artist_ids)}/{len(album_ids)}); skipping", flush=True)
        return

    artist_name = sanitize(music[0]["artist"]["artistName"])
    album_title = sanitize(music[0]["album"]["title"])
    target_dir = os.path.join(MEDIA_ROOT, artist_name, album_title)
    print(f"  artist={artist_name} album={album_title}", flush=True)
    print(f"  target={target_dir}", flush=True)

    os.makedirs(target_dir, exist_ok=True)

    moved = 0
    for entry in os.listdir(src_dir):
        s = os.path.join(src_dir, entry)
        d = os.path.join(target_dir, entry)
        if os.path.exists(d):
            print(f"  skip {entry} (already exists at target)", flush=True)
            continue
        shutil.move(s, d)
        moved += 1
    print(f"  moved {moved} entries", flush=True)

    new_proposals = call_api(
        "GET",
        "/api/v1/manualimport",
        params={"folder": target_dir, "filterExistingFiles": "false"},
    )
    files_payload = []
    for p in new_proposals:
        if not p.get("album") or not p.get("artist") or not p.get("tracks"):
            continue
        files_payload.append({
            "path": p["path"],
            "albumId": p["album"]["id"],
            "artistId": p["artist"]["id"],
            "albumReleaseId": p.get("albumReleaseId"),
            "trackIds": [t["id"] for t in p["tracks"]],
            "quality": p["quality"],
            "indexerFlags": p.get("indexerFlags", 0),
            "disableReleaseSwitching": False,
            "additionalFile": False,
            "replaceExistingFiles": True,
        })

    if not files_payload:
        print("  ERROR: no music files in target after move; aborting", flush=True)
        return

    cmd = call_api(
        "POST",
        "/api/v1/command",
        body={"name": "ManualImport", "files": files_payload, "importMode": "auto"},
    )
    print(f"  ManualImport queued: command-id={cmd['id']} ({len(files_payload)} files)", flush=True)

    try:
        if not os.listdir(src_dir):
            os.rmdir(src_dir)
            print(f"  removed empty source {src_dir}", flush=True)
    except OSError as e:
        print(f"  could not rmdir source: {e}", flush=True)


def main():
    if not os.path.isdir(FAILED_IMPORTS_DIR):
        print(f"no {FAILED_IMPORTS_DIR}; nothing to do")
        return

    entries = sorted(os.listdir(FAILED_IMPORTS_DIR))
    print(f"{len(entries)} failed_imports entries to process")

    for entry in entries:
        src = os.path.join(FAILED_IMPORTS_DIR, entry)
        if not os.path.isdir(src):
            continue
        try:
            process_dir(src)
        except Exception as e:
            print(f"  FAILED on {src}: {type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    main()
