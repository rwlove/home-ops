#!/usr/bin/env python3
"""LightRAG query — grounded retrieval from Rob's knowledge graph.

LOCAL Hermes use only; never forward results into a claude -p prompt.
Usage: query.py "question" [mode] [--context-only]
  mode: local (default) | global | mix | hybrid | naive
"""
import json
import os
import sys
import urllib.request


def main() -> None:
    flags = [a for a in sys.argv[1:] if a.startswith("-")]
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        print('usage: query.py "question" [mode] [--context-only]', file=sys.stderr)
        sys.exit(2)
    query = args[0]
    mode = args[1] if len(args) > 1 else "local"
    context_only = "--context-only" in flags

    url = os.environ.get("LIGHTRAG_URL", "http://lightrag.ai.svc.cluster.local:9621").rstrip("/")
    key = os.environ.get("LIGHTRAG_API_KEY", "")
    body = json.dumps({
        "query": query, "mode": mode, "top_k": 8,
        "max_total_tokens": 6000, "only_need_context": context_only,
    }).encode()
    req = urllib.request.Request(
        url + "/query", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        print("lightrag query failed: %s" % exc, file=sys.stderr)
        sys.exit(1)
    print(data.get("response", data) if isinstance(data, dict) else data)


if __name__ == "__main__":
    main()
