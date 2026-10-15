#!/usr/bin/env python3
"""Weekly Crusher health trend-review digest (local-cron, kagent tier).

Invokes the in-cluster `crusher` medical-steward agent over A2A (via the shared,
poll-to-terminal a2a_client) and asks it to compute, from its READ-ONLY tools, the
trailing blood-pressure trend, the weight<->BP correlation, and any relation to
medication (TRT) dose — then delivers the short interpretation to Robert as a
themed HTML email via notify-mcp (`/send`), the same private sink the sibling
local-cron digests use. notify-mcp holds the relay recipient-lock, so this can
only ever email Robert.

Why this is a digest runner, NOT triage_run.py: triage_run is a problem-only gate
(PROBLEM: -> notify, ALL CLEAR -> silent). A weekly trend review is a DIGEST Robert
wants every week, so this ALWAYS delivers the agent's reply. It only stays silent
when the agent is unreachable or returns nothing (a2a_client never raises -> a
silent skipped run, next week retries, never a KubeJobFailed page).

Data classification: ALL medical reasoning runs on the LOCAL kagent model (qwen on
the Spark via LiteLLM) and every reading/dose/weight stays in-cluster. The ONE
thing that leaves the cluster is the short interpretation text in the email
notify-mcp relays to Robert (a Robert-directed private sink). Never a public sink,
never a remote model. Stdlib only (runs on the shared local-cron python image).

Env: AGENT_URL AGENT_NAME TITLE PROMPT
     [BUDGET_SECONDS=800] [NOTIFY_URL=http://notify-mcp.mcp-system:8060/send]
"""
import datetime
import json
import os
import sys
import urllib.request

import a2a_client

AGENT_URL = os.environ["AGENT_URL"]
AGENT_NAME = os.environ.get("AGENT_NAME", "crusher")
TITLE = os.environ.get("TITLE", "crusher trend review")
BUDGET = int(os.environ.get("BUDGET_SECONDS", "800"))
NOTIFY_URL = os.environ.get("NOTIFY_URL", "http://notify-mcp.mcp-system:8060/send")

# Output contract — owns FORMAT + one-way + never-fabricate + not-a-clinician.
# The per-cron PROMPT supplies only the DOMAIN focus (what to review).
CONTRACT = (
    "\n\nOUTPUT CONTRACT — follow it exactly, it is as important as the task:\n"
    "- Reply in ENGLISH ONLY. This is a ONE-WAY notification: never ask a question "
    "or request input or confirmation — nobody can reply. If unsure, state your "
    "best assessment and proceed.\n"
    "- NEVER fabricate or round a number; every figure must come from a tool. If a "
    "value is unknown, say so — never fill it in.\n"
    "- You are NOT a clinician. Track, trend, and surface what is worth raising with "
    "a doctor; never diagnose, prescribe, or adjust a dose.\n"
    "- Lead with the single headline finding and what it means, then at most a few "
    "short lines: the trailing BP trend, the weight<->BP relationship, and any line-"
    "up with a dose change. End with the one thing to raise at the next visit.\n"
    "- Keep the WHOLE reply under 900 characters. Light markdown is fine (**bold**, "
    "`code`, `- bullets`, short headings) — no tables, no images; it is rendered "
    "into a themed HTML email.\n"
    "- Do NOT call any notify/alert/email tool — THIS job delivers your reply. Just "
    "return the interpretation as your answer."
)


def log(m):
    print(m, flush=True)


def notify_email(subject, body):
    """POST a themed HTML email through notify-mcp's /send route. No auth: notify-mcp
    holds the recipient-lock, so it can only ever email Robert."""
    data = json.dumps({"subject": subject, "body": body, "agent": AGENT_NAME}).encode()
    req = urllib.request.Request(NOTIFY_URL, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.getcode()


def main():
    prompt = os.environ["PROMPT"].strip() + CONTRACT
    day = datetime.date.today().isoformat()

    text, state = a2a_client.call_agent(AGENT_URL, prompt, budget=BUDGET, message_id="crusher-review")

    # Unreachable agent / unreachable model backend / slow-non-terminal / empty:
    # stay silent (the weekly cadence tolerates a skipped run). a2a_client never
    # raises, so this degrades to exit 0, never a KubeJobFailed page.
    if not text:
        log("no agent text (state=%s) — silent skipped run; next week retries" % state)
        log("CRUSHER_REVIEW_RESULT=skipped agent=%s state=%s" % (AGENT_NAME, state))
        return 0

    log("----- %s (state=%s) -----" % (TITLE, state))
    log(text)
    try:
        code = notify_email("%s — %s" % (TITLE, day), text)
        log("digest emailed via notify-mcp (HTTP %s)" % code)
        log("CRUSHER_REVIEW_RESULT=sent agent=%s" % AGENT_NAME)
    except Exception as e:
        # Delivery failed but we HAVE the analysis in the log — surface it as a
        # non-fatal signal; do not hard-fail (next week retries).
        log("WARN: notify-mcp delivery failed: %r" % e)
        log("CRUSHER_REVIEW_RESULT=delivery-failed agent=%s" % AGENT_NAME)
    return 0


if __name__ == "__main__":
    sys.exit(main())
