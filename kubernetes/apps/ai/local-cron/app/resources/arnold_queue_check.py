#!/usr/bin/env python3
"""Arnold training-plan queue check (local-cron, kagent tier).

A cheap, short forgot-to-check-in safety net. Invokes the in-cluster `arnold`
fitness agent over A2A (via the shared a2a_client) and asks it to read Rob's PUMP
queue progress (api.v_plan_progress, read-only via pump-mcp) and DECIDE whether to
remind him to run a training check-in. If a reminder is due, Arnold returns the
short message and THIS runner delivers it to Rob as a themed HTML email via
notify-mcp (/send) — the same private, recipient-locked sink the sibling
local-cron digests use.

Unlike crusher_review.py (a weekly digest that ALWAYS sends), this is a CONDITIONAL
reminder: Arnold replies with the sentinel `NONE` when nothing should be sent
(queue healthy, or already reminded this low-queue episode — dedup/re-arm tracked
in Arnold's memory-mcp), and this runner then stays silent. A slow/empty/unreachable
agent also degrades to a silent skipped run (a2a_client never raises).

Data classification: all reasoning runs on the LOCAL kagent model; the only thing
that leaves the cluster is the short reminder text in the email notify-mcp relays
to Rob — generic training/check-in language, never medical specifics, never a
public sink or remote model. Stdlib only (shared local-cron python image).

Env: AGENT_URL AGENT_NAME TITLE PROMPT
     [BUDGET_SECONDS=300] [NOTIFY_URL=http://notify-mcp.mcp-system:8060/send]
"""
import json
import os
import sys
import urllib.request

import a2a_client

AGENT_URL = os.environ["AGENT_URL"]
AGENT_NAME = os.environ.get("AGENT_NAME", "arnold")
TITLE = os.environ.get("TITLE", "arnold plan-queue check")
BUDGET = int(os.environ.get("BUDGET_SECONDS", "300"))
NOTIFY_URL = os.environ.get("NOTIFY_URL", "http://notify-mcp.mcp-system:8060/send")

# Output contract — owns the one-way / never-fabricate / skip-sentinel rules. The
# per-cron PROMPT supplies the decision logic (when to remind). Mirrors
# crusher_review.py's CONTRACT but for a CONDITIONAL training reminder.
CONTRACT = (
    "\n\nOUTPUT CONTRACT — follow it exactly, it is as important as the task:\n"
    "- Reply in ENGLISH ONLY. This is a ONE-WAY notification: never ask a question "
    "or request input or confirmation — nobody can reply.\n"
    "- You have NO email/notify/alert tool and MUST NOT attempt to send anything "
    "yourself — THIS job delivers your reply. Return ONLY a verdict, nothing else: "
    "no reasoning, no preamble, no 'let me...' narration.\n"
    "- NEVER fabricate a number; the session count must come from your tool "
    "(api.v_plan_progress). If you cannot read it, reply with exactly: NONE\n"
    "- If NO reminder should be sent (queue healthy, or you already reminded during "
    "this low-queue stretch), reply with EXACTLY the single word NONE and nothing "
    "else.\n"
    "- If a reminder IS due, reply with EXACTLY 'REMIND:' (that literal prefix) "
    "followed by the short reminder message for Rob — a few lines in your coaching "
    "voice, GENERIC training/check-in language, NO medical / recovery / TRT "
    "specifics. Put NOTHING before 'REMIND:'.\n"
    "- Keep the whole reply under 600 characters. Light markdown is fine; no tables, "
    "no images."
)


def log(m):
    print(m, flush=True)


def notify_email(subject, body):
    """POST a themed HTML email through notify-mcp's /send route. No auth: notify-mcp
    holds the recipient-lock, so it can only ever email Rob."""
    data = json.dumps({"subject": subject, "body": body, "agent": AGENT_NAME}).encode()
    req = urllib.request.Request(NOTIFY_URL, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.getcode()


def main():
    prompt = os.environ["PROMPT"].strip() + CONTRACT
    text, state = a2a_client.call_agent(AGENT_URL, prompt, budget=BUDGET, message_id="arnold-queue-check")

    # Unreachable / slow / empty: stay silent (weekly cadence tolerates a skip).
    if not text:
        log("no agent text (state=%s) — silent skipped run; next week retries" % state)
        log("ARNOLD_QUEUE_RESULT=skipped agent=%s state=%s" % (AGENT_NAME, state))
        return 0

    # Strict verdict parse: ONLY a leading REMIND: prefix triggers an email, and we
    # email ONLY the message after it. The NONE sentinel, stray reasoning, a refusal,
    # or any other shape stays silent — this guarantees the runner can never email
    # the agent's deliberation (the CoT-leak bug). Belt-and-suspenders with removing
    # Arnold's notify_rob tool (agent-arnold.yaml), so it also can't self-send.
    t = text.strip()
    if not t.upper().startswith("REMIND:"):
        log("no REMIND verdict (NONE / healthy / unparseable) — no email")
        log("ARNOLD_QUEUE_RESULT=none agent=%s state=%s" % (AGENT_NAME, state))
        return 0
    msg = t[len("REMIND:"):].strip()
    if not msg:
        log("REMIND: with empty message — no email")
        log("ARNOLD_QUEUE_RESULT=none agent=%s state=%s" % (AGENT_NAME, state))
        return 0

    log("----- %s (state=%s) -----" % (TITLE, state))
    log(msg)
    try:
        code = notify_email(TITLE, msg)
        log("reminder emailed via notify-mcp (HTTP %s)" % code)
        log("ARNOLD_QUEUE_RESULT=sent agent=%s" % AGENT_NAME)
    except Exception as e:  # noqa: BLE001
        log("WARN: notify-mcp delivery failed: %r" % e)
        log("ARNOLD_QUEUE_RESULT=delivery-failed agent=%s" % AGENT_NAME)
    return 0


if __name__ == "__main__":
    sys.exit(main())
