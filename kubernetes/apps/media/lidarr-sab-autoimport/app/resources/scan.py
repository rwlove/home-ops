#!/usr/bin/env python3
"""
Workaround for Lidarr 3.1.2.4938 (develop) queue-tracker regression.

Lidarr's `Refresh Monitored Downloads` task is supposed to fire
DownloadedAlbumsScan for sab downloads that finish in /sabnzbd/complete/,
and then drop the queue row once the import completes. In 3.1.2.4938
both halves are broken:

  1. Scans don't fire — items sit in queue with status=completed,
     trackedDownloadState=importing forever, and files pile up in
     /sabnzbd/complete.

  2. Queue rows don't clear — even after the import succeeds (manually
     or via the scan-trigger half of this script), the row stays.
     A new scan returns "Importing 0 tracks" because the files are
     already in /media, so the row never drops on its own.

This cron handles both halves:

  - For each stuck row whose album isn't yet fully imported: POST
     DownloadedAlbumsScan against outputPath so Lidarr's normal import
     logic runs (match score, move into /media, etc.).

  - For each stuck row whose album already reports 100% imported: DELETE
     the queue row. The download is done, the files are in /media, the
     row is just stale tracker state.

Remove this whole app when upstream Lidarr fixes both halves — we'll
know because new sab completions will start clearing on their own
within ~1 min of the next `Refresh Monitored Downloads` cycle.
"""
import json
import os
import urllib.parse
import urllib.request

LIDARR_URL = os.environ.get("LIDARR_URL", "http://lidarr.media.svc.cluster.local:8686")
LIDARR_API_KEY = os.environ["LIDARR__API_KEY"]


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
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def main():
    q = call_api(
        "GET",
        "/api/v1/queue",
        params={"pageSize": 100, "includeAlbum": "true"},
    )
    stuck = [
        r for r in q.get("records", [])
        if r.get("status") == "completed"
        and r.get("trackedDownloadState") == "importing"
    ]
    if not stuck:
        print("no stuck queue items")
        return

    scans = set()
    drops = []
    for r in stuck:
        stats = (r.get("album") or {}).get("statistics") or {}
        already_imported = (
            (stats.get("percentOfTracks") or 0) >= 100
            and (stats.get("trackFileCount") or 0) > 0
        )
        if already_imported:
            drops.append(r)
            continue
        path = r.get("outputPath")
        if not path or path in scans:
            continue
        scans.add(path)
        cmd = call_api(
            "POST",
            "/api/v1/command",
            body={"name": "DownloadedAlbumsScan", "path": path, "importMode": "auto"},
        )
        print(f"  scan id={cmd.get('id')} path={path}")

    for r in drops:
        qid = r["id"]
        title = (r.get("album") or {}).get("title") or r.get("title", "?")
        call_api(
            "DELETE",
            f"/api/v1/queue/{qid}",
            params={"removeFromClient": "false", "blocklist": "false"},
        )
        print(f"  drop queue id={qid} album={title!r} (already 100% imported)")

    print(f"queued {len(scans)} scans, dropped {len(drops)} stale queue entries")


if __name__ == "__main__":
    main()
