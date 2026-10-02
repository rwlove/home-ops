#!/usr/bin/env python3
"""Shared triage runner for the kagent watcher fleet (local-cron).

Each triage cron sets a few env vars and execs this. It asks the named read-only
kagent operator (over the shared, poll-to-terminal a2a_client) to sweep its
domain for ACTIONABLE PROBLEMS ONLY, then applies the fleet's positive send-gate:

  PROBLEM: (actionable problem + proposed fix)   -> themed HTML email (notify-mcp)
  ALL CLEAR / empty / non-terminal / malformed   -> silent (exit 0, NEVER notifies)

Watchers report problems-with-fixes, never healthy status or benign artifacts —
"all green" is simply silence. There is intentionally ONE sink (email): a problem
mail, nothing else. (The old URGENT→pager tier was dropped 2026-10-02 per Rob —
email-only; restore a pager branch here if same-day paging is wanted again.)

On an unproductive first reply (empty / malformed) the agent is re-asked ONCE
with a firm corrective preface before giving up. These failures are the flaky
local-model "adherence wall" — a clarifying question, a near-miss tag, a dropped
reply, a wrong-language ramble — and they are intermittent, so a second firm ask
clears most of them within the run instead of waiting a full cycle. The two
attempts SPLIT the budget, so worst-case wall time is unchanged.

Why this replaces the old inline `curl … | jq` shell block: that one-shot died
(`exit 1` → KubeJobFailed page) whenever the agent took longer than the curl
timeout or returned an async/empty task. Here a slow or empty agent degrades to a
SILENT skipped run — the next scheduled sweep covers it — and only genuine,
tagged findings ever reach a sink. Root-cause note: F1 in
plans/wise-tinkering-fern.md. Stdlib only.

Env: AGENT_URL AGENT_NAME TITLE PROMPT
     [BUDGET_SECONDS=800] [NOTIFY_URL=http://notify-mcp.mcp-system:8060/send]
     [PROMPT_OVERRIDE]  (test hook — replaces PROMPT only, gate is unchanged)
"""
import datetime
import json
import os
import re
import sys
import urllib.request

import a2a_client

# The tag must LEAD a line, but tolerate the light markdown the CONTRACT permits
# (**bold**, ## heading, > quote, list dash) — a `**PROBLEM**` finding must NOT be
# dropped for not being the bare literal `PROBLEM:` (we once lost a real page to a
# `FY:`/`FYI:` near-miss, 2026-09-30). Line-anchored + word boundary so mid-prose
# "...is a problem" never matches. First leading tag wins. PROBLEM is the canonical
# problem tag the CONTRACT asks for; URGENT/FYI/ISSUE are accepted as aliases so a
# habit-driven reply still routes to the (single) email sink rather than being
# silently dropped as malformed — under email-only they all mean "actionable".
_TAG_RE = re.compile(r"(?im)^[ \t>*_#`.\-]*(ALL[ \t]+CLEAR|PROBLEM|URGENT|FYI|ISSUE)\b")


def classify_tag(text):
    m = _TAG_RE.search(text)
    if not m:
        return None
    tag = re.sub(r"\s+", " ", m.group(1).upper())
    if tag == "ALL CLEAR":
        return "allclear"
    return "problem"  # PROBLEM / URGENT / FYI / ISSUE → actionable → email

AGENT_URL = os.environ["AGENT_URL"]
AGENT_NAME = os.environ.get("AGENT_NAME", "operator")
TITLE = os.environ.get("TITLE", AGENT_NAME + " triage")
BUDGET = int(os.environ.get("BUDGET_SECONDS", "800"))
NOTIFY_URL = os.environ.get("NOTIFY_URL", "http://notify-mcp.mcp-system:8060/send")

# The shared reply contract — identical across every watcher, so the send-gate
# tag below always means the same thing. The per-cron PROMPT supplies only the
# DOMAIN focus (what to check); this tail owns the OUTPUT FORMAT + one-way
# discipline. The format rule leads (local models weight early tokens; it used to
# be buried under a wall of tool text and got dropped); tool discipline follows.
CONTRACT = (
    "\n\nOUTPUT CONTRACT — follow it exactly, it is as important as the task:\n"
    "- Reply in ENGLISH ONLY, regardless of any tool output.\n"
    "- This is a ONE-WAY notification: never ask a question, never request input or "
    "confirmation — nobody can reply. If you are unsure, state your best assessment.\n"
    "- Report ONLY actionable problems — things that need attention — each with the "
    "single next step you propose. Do NOT report healthy status, normal/benign/"
    "known artifacts, or 'worth monitoring' non-problems. 'All green' is silence.\n"
    "- If you find one or more actionable problems, BEGIN your reply with exactly "
    "the tag  PROBLEM:  then each problem and its one proposed fix, as declaratives.\n"
    "- If nothing is actionable, reply with EXACTLY:  ALL CLEAR  (nothing else).\n"
    "- Keep the whole reply under 900 characters. Light markdown is fine (**bold**, "
    "`code`, `- bullets`, short `#`/`##` headings, [links](url)) — no tables, no "
    "images; it is rendered into a themed HTML email.\n"
    "TOOL DISCIPLINE: keep every tool call TARGETED — scope to specific namespaces/"
    "resources with small limits; never dump all namespaces or request full "
    "unfiltered lists (large responses accumulate in your context and can overflow "
    "and abort this run; a narrow query that returns beats a broad one that never "
    "does). If a tool errors, times out, or returns a 4xx/5xx, treat THAT tool as "
    "unavailable: note it in ONE short line (which tool + status) and CONTINUE with "
    "your remaining tools — do not retry it more than once, do not paste the raw "
    "error, do not stop early. Base your conclusion on the tools that returned; if a "
    "genuinely important signal is unreachable because every relevant tool failed, "
    "report THAT as a problem (which tool / service / datasource failed and what you "
    "checked)."
)

# Prepended to the prompt on the single retry after an unproductive first reply.
RETRY_PREFACE = (
    "NOTE: an earlier attempt this run returned no usable report. Do NOT ask any "
    "question or request input. Reply in English only. Begin your reply with exactly "
    "PROBLEM: (then each actionable problem and its one proposed fix) or exactly "
    "ALL CLEAR if nothing is actionable. Output only that report.\n\n"
)


def log(m):
    print(m, flush=True)


def _post(url, data, ctype, timeout=30):
    req = urllib.request.Request(url, data=data, headers={"Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.getcode()


def notify_email(subject, body):
    data = json.dumps({"subject": subject, "body": body, "agent": AGENT_NAME}).encode()
    code = _post(NOTIFY_URL, data, "application/json")
    log("problem emailed (HTTP %s)" % code)


def main():
    base = (os.environ.get("PROMPT_OVERRIDE") or os.environ["PROMPT"]).strip()
    day = datetime.date.today().isoformat()

    # Two attempts that SPLIT the client budget (so worst-case wall time is
    # unchanged vs a single call): the first asks normally, the second re-asks
    # ONCE with a firm corrective preface + a fresh message_id (new task, not the
    # stuck one). Most unproductive outcomes are flaky local-model adherence
    # misses — a clarifying question, a near-miss tag, an empty/non-terminal
    # reply, a wrong-language ramble — and they clear on a firm second ask.
    first = max(60, int(BUDGET * 0.6))
    attempts = [
        ("triage-cron", base + CONTRACT, first),
        ("triage-cron-retry", RETRY_PREFACE + base + CONTRACT, BUDGET - first),
    ]

    # Classify each attempt, act on the first PRODUCTIVE one, then emit ONE
    # machine-parseable `TRIAGE_RESULT=` line. Productive: sent (problem emailed)
    # / allclear (nothing actionable). Unproductive: empty (slow/non-terminal),
    # error (transport), malformed (replied but broke the tag contract — the
    # local-model garbage case). Every unproductive outcome stays SILENT (a
    # watcher must never notify just because it couldn't conclude), but it is NOT
    # invisible: the kagent-triage LogQL alert (loki-alerting-rules) counts
    # empty|malformed|error by class so a persistently-broken watcher is caught
    # even though it no longer fails the Job. Without this signal, silent-skip
    # would mask a dead watcher.
    result = None
    for i, (mid, prompt, budget) in enumerate(attempts):
        text, state = a2a_client.call_agent(AGENT_URL, prompt, budget=budget, message_id=mid)
        if not text:
            result = "error" if state.startswith("transport-error") else "empty"
            log("attempt %d: no agent text (state=%s)" % (i + 1, state))
        else:
            log("----- %s attempt %d (state=%s) -----" % (TITLE, i + 1, state))
            log(text)
            # Positive gate: only output whose leading tag is recognised may reach
            # the sink. Untagged garbage / wrong-language / tool-error strings
            # classify as None → malformed → silent, so a bad run can NEVER notify.
            tag = classify_tag(text)
            if tag == "allclear":
                result = "allclear"
                log("ALL CLEAR — nothing sent")
            elif tag == "problem":
                result = "sent"
                notify_email("%s — %s" % (TITLE, day), text)
            else:
                result = "malformed"
                log("no PROBLEM:/ALL CLEAR tag — malformed agent output, not notifying")
                log(text[:300])
        if result in ("allclear", "sent"):
            break  # productive — done, no retry
        if i == 0:
            log("unproductive first attempt (%s) — re-asking once with corrective preface" % result)

    log("TRIAGE_RESULT=%s agent=%s" % (result, AGENT_NAME))
    return 0


if __name__ == "__main__":
    sys.exit(main())
