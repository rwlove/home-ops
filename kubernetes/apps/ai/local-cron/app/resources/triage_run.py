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

Two failure modes get two different retries:

  * MODEL BACKEND UNAVAILABLE — the agent can't reach its model (GPU busy/down on
    the P40 / Spark, or LiteLLM rolling): a transport error to the agent, or a
    `failed` task whose text names an upstream model/transport error. This is
    RETRYABLE. The run is NOT dropped — we back off and re-ask the SAME agent, in a
    loop, until the GPU model is serving again or the BACKEND_RETRY_SECONDS window
    is spent. "Missing GPU → go back in the queue, retry when available." The
    agent's own model call is the GPU gate: once the backend serves, the call
    succeeds. Only if the window is exhausted does the run terminate as `error`
    (so a terminal `error` means "GPU unavailable AND the job already failed
    repeatedly") — that, recurring, is the one thing worth paging on.

  * ADHERENCE MISS — the backend WAS reachable but the reply is empty / malformed
    (a clarifying question, a near-miss tag, a dropped reply, a wrong-language
    ramble): the flaky local-model "adherence wall." The agent is re-asked ONCE
    with a firm corrective preface, then the run gives up SILENTLY (the next
    scheduled sweep covers it). The two adherence attempts SPLIT the budget.

Backend-unavailability classifies as `error` (transport), never `malformed` —
`malformed` is reserved for a reachable model that broke the tag contract. This
keeps the kagent-triage alert naming the real cause (GPU vs model drift).

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
import time
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
# The trailing capture holds the rest of the tag's line so a "PROBLEM: None
# detected … ALL CLEAR" near-miss (a healthy narrative mislabelled PROBLEM, seen
# 2026-10-04 from the strong-tier network-operator) can be demoted to allclear
# instead of paging. `.search()` still takes the FIRST leading tag.
_TAG_RE = re.compile(
    r"(?im)^[ \t>*_#`.\-]*(ALL[ \t]+CLEAR|PROBLEM|URGENT|FYI|ISSUE)\b(.*)$"
)
# A problem tag whose line immediately continues with a no-op negation is not a
# real finding — treat it as allclear (silent) rather than emailing.
_NEGATION = re.compile(
    r"(?i)^[ \t:*_`.–—-]*(none|no\b|nothing|n/?a|all[ \t]*clear|no problem|no issue)"
)


def classify_tag(text):
    m = _TAG_RE.search(text)
    if not m:
        return None
    tag = re.sub(r"\s+", " ", m.group(1).upper())
    if tag == "ALL CLEAR" or _NEGATION.match(m.group(2)):
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
    prompt_first = base + CONTRACT
    prompt_retry = RETRY_PREFACE + base + CONTRACT

    # Per-call budget. The two ADHERENCE attempts (first + one corrective re-ask)
    # SPLIT the budget, so their combined wall time stays ≤ BUDGET.
    per_call = max(60, int(BUDGET * 0.5))

    # BACKEND REQUEUE WINDOW. When the agent can't reach its model (GPU busy/down,
    # LiteLLM rolling) the run is NOT dropped: we back off and re-ask the SAME agent
    # until the GPU model is serving again or this window is spent. The job's
    # activeDeadlineSeconds must exceed BACKEND_RETRY_SECONDS + BUDGET (the triage
    # CronJobs set 2100 for a 900s window + 800s budget + slack). Default 900s =
    # spans a short rollout/cold-start/VRAM-thrash blip; a longer GPU outage runs
    # the window out, emits a terminal `error`, and the next 6h sweep retries.
    backend_window = int(os.environ.get("BACKEND_RETRY_SECONDS", "900"))
    deadline = time.monotonic() + backend_window
    backoff = 20

    # Act on the first PRODUCTIVE reply, then emit ONE machine-parseable
    # `TRIAGE_RESULT=` line. Productive: sent (problem emailed) / allclear (nothing
    # actionable). Unproductive and SILENT (a watcher must never notify just because
    # it couldn't conclude): empty (reachable but slow/non-terminal), malformed
    # (reachable but broke the tag contract — local-model drift), error (backend/GPU
    # unavailable through the whole requeue window). Only `error`, recurring, pages —
    # see the kagent-triage LogQL rule; empty/malformed stay log-only.
    result = None
    drift_retry_used = False
    while True:
        prompt = prompt_retry if drift_retry_used else prompt_first
        mid = "triage-cron-retry" if drift_retry_used else "triage-cron"
        text, state = a2a_client.call_agent(AGENT_URL, prompt, budget=per_call, message_id=mid)

        # Missing GPU / unreachable model backend → requeue: wait and retry the same
        # agent until the model is back or the window is spent.
        if a2a_client.is_backend_unavailable(text, state):
            remaining = int(deadline - time.monotonic())
            if remaining <= 0:
                result = "error"
                log("backend/model unavailable (state=%s) and the %ds requeue "
                    "window is spent — giving up this cycle (error); next sweep "
                    "retries" % (state, backend_window))
                break
            wait = min(backoff, max(1, remaining))
            log("backend/model unavailable (state=%s) — GPU likely busy/down; "
                "requeuing, retry in %ds (%ds of window left)" % (state, wait, remaining))
            time.sleep(wait)
            backoff = min(backoff * 2, 120)
            continue

        if not text:
            # Reached the backend but no usable text — slow / non-terminal agent.
            result = "empty"
            log("no agent text (state=%s)" % state)
        else:
            log("----- %s (state=%s) -----" % (TITLE, state))
            log(text)
            # Positive gate: only output whose leading tag is recognised may reach
            # the sink. Untagged garbage / wrong-language strings classify as None →
            # malformed → silent, so a bad run can NEVER notify.
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
            break  # productive — done
        # Unproductive but the backend WAS reachable (adherence miss, not a GPU
        # outage): re-ask ONCE with a corrective preface, then give up silently.
        if not drift_retry_used:
            drift_retry_used = True
            log("unproductive (%s), backend reachable — re-asking once with corrective preface" % result)
            continue
        break

    log("TRIAGE_RESULT=%s agent=%s" % (result, AGENT_NAME))
    return 0


if __name__ == "__main__":
    sys.exit(main())
