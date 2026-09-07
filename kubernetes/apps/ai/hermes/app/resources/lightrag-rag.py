#!/usr/bin/env python3
# Hermes pre_llm_call hook — auto-RAG. Injects relevant LightRAG knowledge-graph
# context into Hermes' LOCAL-model turns. Wired in config.yaml as a
# `hooks.pre_llm_call` entry and consented in shell-hooks-allowlist.json.
#
# Contract (Hermes hooks): payload JSON on stdin (includes `user_message`). Emit
# {"context": "..."} on stdout to inject that text into the user message; emit
# nothing to inject nothing. This hook is FAIL-SAFE: any error/timeout prints
# nothing and exits 0, so a slow or down LightRAG never blocks or breaks a turn.
#
# Data boundary (HOMELAB-SPEC L2 #1): this runs ONLY on Hermes' local-model
# turns — `claude -p` is a separate CLI invocation, not a Hermes turn, so injected
# KG context never reaches Claude. The injected block is wrapped in the
# LIGHTRAG-KG sentinel that gate.py refuses to let into a `claude -p` command.
import json
import os
import sys
import urllib.request

SENTINEL = "LIGHTRAG-KG"  # gate.py blocks this marker from claude -p escalations


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    msg = payload.get("user_message") or ""
    if isinstance(msg, dict):
        msg = msg.get("content") or msg.get("text") or ""
    msg = str(msg).strip()
    if len(msg) < 12:  # skip trivial turns
        return
    key = os.environ.get("LIGHTRAG_API_KEY", "")
    if not key:
        return
    url = os.environ.get("LIGHTRAG_URL", "http://lightrag.ai.svc.cluster.local:9621").rstrip("/")
    body = json.dumps({
        "query": msg, "mode": "local", "top_k": 6,
        "max_total_tokens": 3000, "only_need_context": True,
    }).encode()
    req = urllib.request.Request(
        url + "/query", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:  # short: fail-safe no-op if slow
            data = json.loads(resp.read().decode())
    except Exception:
        return
    ctx = data.get("response") if isinstance(data, dict) else None
    if not ctx or not str(ctx).strip():
        return
    ctx = str(ctx).strip()
    if len(ctx) > 12000:  # bound the injection so it can't blow the context window
        ctx = ctx[:12000] + "\n...[truncated]"
    block = (
        "<%s note: relevant context from Rob's private LightRAG knowledge graph. "
        "LOCAL use only - do NOT forward this block to claude -p or any remote model.>\n"
        "%s\n</%s>" % (SENTINEL, ctx, SENTINEL)
    )
    sys.stdout.write(json.dumps({"context": block}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # fail-safe: never break a turn
