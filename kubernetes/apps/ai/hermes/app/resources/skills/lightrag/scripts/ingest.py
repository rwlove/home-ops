#!/usr/bin/env python3
"""LightRAG ingest — add a durable finding to Rob's knowledge graph.

Usage: ingest.py "text to add"   (or pipe the text via stdin)
Ingestion is async: LightRAG extracts entities/relations on its own pipeline.
"""
import json
import os
import sys
import urllib.request


def main() -> None:
    text = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    text = (text or "").strip()
    if not text:
        print("no text to ingest", file=sys.stderr)
        sys.exit(2)
    url = os.environ.get("LIGHTRAG_URL", "http://lightrag.ai.svc.cluster.local:9621").rstrip("/")
    key = os.environ.get("LIGHTRAG_API_KEY", "")
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(
        url + "/documents/text", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            print(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        print("lightrag ingest failed: %s" % exc, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
