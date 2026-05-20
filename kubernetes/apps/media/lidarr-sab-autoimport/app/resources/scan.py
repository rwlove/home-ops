#!/usr/bin/env python3
"""
Workaround for Lidarr 3.1.2.4938 (develop) queue-tracker regression.

Lidarr's `Refresh Monitored Downloads` task is supposed to fire
DownloadedAlbumsScan for sab downloads that finish in /sabnzbd/complete/.
In 3.1.2.4938 it doesn't — items sit in the queue with
status=completed, trackedDownloadState=importing forever, the actual
files pile up in /sabnzbd/complete, and the user has to manually
trigger the scan in the Lidarr UI.

This cron kicks Lidarr by POSTing DownloadedAlbumsScan for each stuck
queue item's outputPath. Lidarr's normal import logic does the rest
(match score, move into /media library, etc.). The queue entry then
drops once the import completes.

Remove this whole app when upstream Lidarr fixes the regression — we'll
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
        return json.loads(resp.read())


def main():
    q = call_api("GET", "/api/v1/queue", params={"pageSize": 100})
    stuck = [
        r for r in q.get("records", [])
        if r.get("status") == "completed"
        and r.get("trackedDownloadState") == "importing"
        and r.get("outputPath")
    ]
    if not stuck:
        print("no stuck queue items")
        return

    seen = set()
    for r in stuck:
        path = r["outputPath"]
        if path in seen:
            continue
        seen.add(path)
        cmd = call_api(
            "POST",
            "/api/v1/command",
            body={"name": "DownloadedAlbumsScan", "path": path, "importMode": "auto"},
        )
        print(f"  scan id={cmd.get('id')} path={path}")
    print(f"queued {len(seen)} scans")


if __name__ == "__main__":
    main()
