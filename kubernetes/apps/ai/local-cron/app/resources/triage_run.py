#!/usr/bin/env python3
"""Shared triage runner for the kagent watcher fleet (local-cron).

Each triage cron sets a few env vars and execs this. It asks the named read-only
kagent operator (over the shared, poll-to-terminal a2a_client) to sweep its
domain, then applies the fleet's positive send-gate:

  ALL CLEAR / empty / non-terminal / malformed  -> silent (exit 0, NEVER pages)
  URGENT:                                        -> Pushover pager
  FYI: (or other recognised tag)                 -> themed HTML email (notify-mcp)

Why this replaces the old inline `curl … | jq` shell block: that one-shot died
(`exit 1` → KubeJobFailed page) whenever the agent took longer than the curl
timeout or returned an async/empty task. Here a slow or empty agent degrades to a
SILENT skipped run — the next scheduled sweep covers it — and only genuine,
tagged findings ever reach a sink. Root-cause note: F1 in
plans/wise-tinkering-fern.md. Stdlib only.

Env: AGENT_URL AGENT_NAME TITLE PROMPT
     [BUDGET_SECONDS=800] [NOTIFY_URL=http://notify-mcp.mcp-system:8060/send]
     PUSHOVER_TOKEN PUSHOVER_USER
     [PROMPT_OVERRIDE]  (test hook — replaces PROMPT only, gate is unchanged)
"""
import datetime
import json
import os
import sys
import urllib.parse
import urllib.request

import a2a_client

AGENT_URL = os.environ["AGENT_URL"]
AGENT_NAME = os.environ.get("AGENT_NAME", "operator")
TITLE = os.environ.get("TITLE", AGENT_NAME + " triage")
BUDGET = int(os.environ.get("BUDGET_SECONDS", "800"))
NOTIFY_URL = os.environ.get("NOTIFY_URL", "http://notify-mcp.mcp-system:8060/send")
PUSH_TOKEN = os.environ.get("PUSHOVER_TOKEN", "")
PUSH_USER = os.environ.get("PUSHOVER_USER", "")

# The shared reply contract — identical across every watcher, so the send-gate
# tags below always mean the same thing. The per-cron PROMPT supplies only the
# DOMAIN focus (what to check); this tail owns the format + one-way discipline.
CONTRACT = (
    " If any of your tools errors or times out, do NOT emit generic troubleshooting "
    "steps or ask for more information — nobody can reply. Diagnose the failure "
    "yourself with your OTHER read-only tools (the failing component's pod health, "
    "recent events, logs, and related metrics) and report the SPECIFIC finding: "
    "which tool / service / datasource failed, the exact error, and what your own "
    "check showed. A failed tool is something to investigate, never to hand back. "
    "Classify overall urgency and BEGIN your reply with exactly one tag: URGENT: for "
    "a finding needing same-day attention (real outage, data-loss risk, or safety "
    "issue), or FYI: for something noteworthy but not urgent (worth knowing, no "
    "rush). If nothing is notable, reply with EXACTLY: ALL CLEAR (no tag). After the "
    "tag, state findings and the single proposed next step as declaratives. This is "
    "a one-way notification — never ask a question or request confirmation (nobody "
    "can reply). Keep the entire reply under 900 characters. You may use light "
    "markdown — **bold**, `code`, `- bullets`, short `#`/`##` headings, "
    "[links](url) — it is rendered into a themed HTML email; do not use tables or "
    "images."
)


def log(m):
    print(m, flush=True)


def _post(url, data, ctype, timeout=30):
    req = urllib.request.Request(url, data=data, headers={"Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.getcode()


def pushover(title, message):
    data = urllib.parse.urlencode({
        "token": PUSH_TOKEN, "user": PUSH_USER,
        "title": title[:250], "message": message[:1000],
    }).encode()
    code = _post("https://api.pushover.net/1/messages.json", data, "application/x-www-form-urlencoded")
    log("Pushover pager sent (HTTP %s)" % code)


def notify_email(subject, body):
    data = json.dumps({"subject": subject, "body": body, "agent": AGENT_NAME}).encode()
    code = _post(NOTIFY_URL, data, "application/json")
    log("themed FYI emailed (HTTP %s)" % code)


def main():
    prompt = os.environ.get("PROMPT_OVERRIDE") or os.environ["PROMPT"]
    prompt = prompt.strip() + CONTRACT

    text, state = a2a_client.call_agent(AGENT_URL, prompt, budget=BUDGET, message_id="triage-cron")
    day = datetime.date.today().isoformat()

    # No usable answer (slow/empty/non-terminal/transport error) → SILENT skip.
    # The next scheduled run covers it; a watcher must never page just because it
    # could not reach a conclusion this cycle.
    if not text:
        log("no agent text (state=%s) — silent skip, next run covers it" % state)
        return 0

    log("----- %s (state=%s) -----" % (TITLE, state))
    log(text)

    if "ALL CLEAR" in text:
        log("ALL CLEAR — nothing sent")
        return 0

    # Positive gate: only output carrying a recognised urgency tag may reach a
    # sink. Untagged local-model garbage / wrong-language / tool-error strings are
    # malformed — log a snippet and exit clean so a bad run can NEVER notify Rob.
    if "URGENT:" in text:
        pushover("kagent %s (URGENT) %s" % (TITLE, day), text)
    elif "FYI:" in text:
        notify_email("%s — %s" % (TITLE, day), text)
    else:
        log("no URGENT:/FYI: tag — malformed agent output, not notifying")
        log(text[:300])
    return 0


if __name__ == "__main__":
    sys.exit(main())
