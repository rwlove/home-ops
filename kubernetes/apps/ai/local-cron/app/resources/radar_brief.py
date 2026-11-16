#!/usr/bin/env python3
"""Daily Radar morning-brief digest (local-cron, kagent tier).

Invokes the in-cluster `radar` chief-of-staff agent over A2A (via the shared,
poll-to-terminal a2a_client) and asks it to assemble Robert's morning brief
from already-grounded, structured records ONLY — the Vikunja Fleet Todos
backlog (primary content), today's + this week's concierge-calendar events,
and open home-ops PRs (an informational footer) — then delivers the brief to
Robert as a themed HTML email via notify-mcp (`/send`), the same private
sink the sibling local-cron digests use. notify-mcp holds the relay
recipient-lock, so this can only ever email Robert.

Why this is a digest runner, NOT triage_run.py: triage_run is a problem-only
gate (PROBLEM: -> notify, ALL CLEAR -> silent). A morning brief is a DIGEST
Robert wants every day, so this ALWAYS delivers the agent's reply. It only
stays silent when the agent is unreachable or returns nothing (a2a_client
never raises -> a silent skipped run, tomorrow retries, never a
KubeJobFailed page).

The Vikunja Fleet Todos store (the laptop `whats-next` skill's Source 3) is
federated in-cluster via vikunja-mcp (home-ops #15962) and is this brief's
PRIMARY content as of this file's authoring — see agent-radar.yaml's header
comment for the wiring. Local plan files (Source 2) remain a laptop
filesystem artifact with no in-cluster MCP tool, but that source never fed
`whats-next --brief` mode either, so there is no gap line to carry for it.
The agent's own systemPrompt instructs it to name a genuinely unreachable
source by name rather than invent or silently omit it; this runner's
`_ensure_gap_line()` is a narrow backstop for exactly that case (see below),
not a permanent "no tool" caveat.

`_clean()` exists for the same reason as crusher_review.py's: the local model
can narrate its tool use inline ("Let me check the calendar... done, now
PRs..."). The CONTRACT asks the agent to wrap its final answer in
<<<DIGEST>>>...<<<END>>> markers emitted once (primary path); the fallback
strips stray markers + narration lines and de-dupes repeated blocks. Markers
never reach the email.

Data classification: ALL reasoning runs on the LOCAL kagent model (qwen3.6 on
the Spark via LiteLLM, modelConfig `strong`) and every calendar/PR read stays
in-cluster. The ONE thing that leaves the cluster is the short brief text in
the email notify-mcp relays to Robert (a Robert-directed private sink). Never
a public sink, never a remote model. Stdlib only (runs on the shared
local-cron python image).

Idempotency posture: matches crusher_review.py / the sibling digest runners —
`concurrencyPolicy: Forbid` + `backoffLimit: 1` + a fresh per-run A2A session
(a2a_client appends a uuid4 per call) is the accepted at-least-once posture
for this whole digest family; a crash between a successful notify_email() and
clean exit could in principle double-send, same residual risk the siblings
carry and have not hit in production. Not re-solved here to keep this runner
consistent with its siblings rather than introducing a one-off mechanism.

Env: AGENT_URL AGENT AGENT_NAME TITLE PROMPT
     [BUDGET_SECONDS=800] [NOTIFY_URL=http://notify-mcp.mcp-system:8060/send]
"""
import datetime
import os
import re
import sys

import a2a_client

AGENT_URL = os.environ["AGENT_URL"]
# The real kagent Agent CR name (namespace `ai`) to invoke via AGENT_URL's MCP
# endpoint — distinct from AGENT_NAME (notify/title label only).
AGENT = os.environ["AGENT"]
AGENT_NAME = os.environ.get("AGENT_NAME", "radar")
TITLE = os.environ.get("TITLE", "morning brief")
BUDGET = int(os.environ.get("BUDGET_SECONDS", "800"))
NOTIFY_URL = os.environ.get("NOTIFY_URL", "http://notify-mcp.mcp-system:8060/send")

# Output contract — owns FORMAT + one-way + never-fabricate + sourced-or-omitted.
# The per-cron PROMPT supplies only the DOMAIN focus (what to assemble); the
# agent's own systemPrompt (agent-radar.yaml) already carries the detailed
# gather/rank instructions, so this PROMPT is deliberately short.
CONTRACT = (
    "\n\nOUTPUT CONTRACT — follow it exactly, it is as important as the task:\n"
    "- Reply in ENGLISH ONLY. This is a ONE-WAY notification: never ask a question "
    "or request input or confirmation — nobody can reply. If unsure, state your "
    "best assessment and proceed.\n"
    "- NEVER invent a date, a todo, a PR, or a finding; every line must come from "
    "a tool call you actually made. If a source errors or returns nothing where "
    "you expected data, say so by name (write the literal phrase 'Vikunja "
    "unreachable' if the Vikunja calls are the ones that failed) — never omit the "
    "gap silently, never fill it with a guess.\n"
    "- STRUCTURE the reply so it SCANS — never one prose paragraph. Emit, in order:\n"
    "  (a) a `### Today` heading. List Vikunja tasks due today or overdue FIRST, "
    "ranked by priority P5 to P1 then soonest due date, flagging any `robert-action` "
    "label; if NOTHING is due today or overdue, fall back to the top 3-5 highest-"
    "priority OPEN tasks instead so this section is never empty. Then list today's "
    "calendar events.\n"
    "  (b) a `### This week` heading. List Vikunja tasks due within the next 7 days "
    "(same P5-to-P1 then soonest-due ranking, excluding anything already listed "
    "under Today), then calendar events in that window. Omit this heading entirely "
    "if nothing qualifies.\n"
    "  (c) a final `### Open PRs awaiting you` heading — a short, PLAIN, UNRANKED "
    "list of open home-ops PRs (not folded into Today/This week). Note each PR's "
    "CI state if the tool surfaces it; otherwise say 'CI state not available' "
    "rather than guessing green/red. Omit this heading if there are no open PRs.\n"
    "  Do NOT write the labels (a)/(b)/(c) in the reply.\n"
    "- Keep the WHOLE reply under 900 characters. Use **bold**, `- bullets`, `code`, "
    "and short `#`/`##`/`###` headings — no tables, no images; it is rendered into a "
    "themed HTML email.\n"
    "- Do NOT include any of your working in the reply: no tool-call commentary, "
    "no self-correction. Only the finished brief, written once.\n"
    "- Wrap your ENTIRE final reply between a line containing exactly <<<DIGEST>>> "
    "and a line containing exactly <<<END>>>, output that block EXACTLY ONCE, and "
    "put nothing before or after the markers.\n"
    "- Do NOT call any notify/alert/email tool — THIS job delivers your reply. Just "
    "return the brief as your answer."
)

_DIGEST_START = "<<<DIGEST>>>"
_DIGEST_END = "<<<END>>>"

_NARRATION_RE = re.compile(
    r"^\s*(?:I|I'm|I've|I'll|I'd|Let me|Let's|Now|Okay|OK|Wait|Actually|First|Next|"
    r"Then|Hmm|So I|Looking|Checking|Searching|Pulling|Gathering)\b",
    re.IGNORECASE,
)

# F2 — narrow backstop, scoped DOWN from the old always-fire gap line now
# that Vikunja is wired as the brief's PRIMARY content (home-ops #15962 +
# this PR). An empty Today/This week section because nothing is due is a
# valid, honest brief — NOT a gap to paper over, so this must NOT fire by
# default. It fires ONLY when the model's own reply already shows evidence
# Vikunja itself failed (the systemPrompt instructs it to say so by name,
# e.g. "Vikunja unreachable" — a weak local model can bury that admission in
# prose instead of a clean, scannable line, the same class of omission
# crusher_review.py's memory-write backstop exists for). Cheap sentinel
# check, NOT fabrication detection.
_UNREACHABLE_RE = re.compile(
    r"vikunja[^.\n]{0,80}(unreachable|unavailable|error|failed|timed out|timeout)",
    re.IGNORECASE,
)
_CANNED_UNREACHABLE_NOTE = (
    "\n\n_Vikunja Fleet Todos backlog unreachable this run — showing calendar/"
    "PR items only; see the laptop `whats-next --brief` run for the backlog._"
)


def _ensure_gap_line(text):
    s = text or ""
    if not _UNREACHABLE_RE.search(s):
        return text
    if "backlog unreachable" in s.lower():
        return text
    log("WARN: agent reply shows Vikunja unreachable — appending the clarifying note")
    return s.rstrip() + _CANNED_UNREACHABLE_NOTE


def log(m):
    print(m, flush=True)


def _dedupe_blocks(text):
    seen = set()
    out = []
    for para in re.split(r"\n\s*\n", text):
        norm = re.sub(r"\s+", " ", para).strip().lower()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(para.strip())
    return "\n\n".join(out)


def _clean(text):
    """Reduce the agent's raw reply to the single digest block we email. See
    crusher_review.py's `_clean()` for the full rationale — same contract."""
    if not text or not text.strip():
        return text
    s = text.strip()

    if _DIGEST_START in s:
        start = s.rfind(_DIGEST_START) + len(_DIGEST_START)
        end = s.find(_DIGEST_END, start)
        inner = (s[start:end] if end != -1 else s[start:]).strip()
        if inner:
            return inner

    lines = [
        ln for ln in s.splitlines()
        if _DIGEST_START not in ln and _DIGEST_END not in ln
        and not _NARRATION_RE.match(ln)
    ]
    cleaned = _dedupe_blocks("\n".join(lines).strip())
    return cleaned or s


def notify_email(subject, body):
    """POST a themed HTML email through notify-mcp's /send route. No auth: notify-mcp
    holds the recipient-lock, so it can still only ever email Robert."""
    import json
    import urllib.request
    data = json.dumps({"subject": subject, "body": body, "agent": AGENT_NAME}).encode()
    req = urllib.request.Request(NOTIFY_URL, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.getcode()


def main():
    # The container's TZ=America/New_York env makes date.today() the correct
    # ET calendar date (same tz handling the email-subject `day` below already
    # relies on). Inject the REAL date + an explicit window into the prompt so
    # the model never guesses "today" — F3: a weak local model asked to find
    # "today's" events with no date anchor will happily hallucinate one.
    # Window is 7 days (the "This week" section), matching the laptop
    # `whats-next` skill's own Source 4 calendar pull — widened from the
    # original 3-day "Coming up" window when PRs were demoted to a footer
    # and Vikunja due-dates became the lead content.
    today = datetime.date.today()
    window_end = today + datetime.timedelta(days=7)
    date_context = (
        "Today's actual date is %s (America/New_York). Treat %s through %s "
        "inclusive as the 'This week' window for both calendar events and "
        "Vikunja task due dates. Do NOT assume, guess, or carry over any other "
        "date — use exactly this one.\n\n"
        % (today.isoformat(), today.isoformat(), window_end.isoformat())
    )
    prompt = date_context + os.environ["PROMPT"].strip() + CONTRACT
    day = today.isoformat()

    text, state = a2a_client.call_agent(AGENT_URL, AGENT, prompt, budget=BUDGET, message_id="radar-brief")
    text = _clean(text)

    if not text:
        log("no agent text (state=%s) — silent skipped run; tomorrow retries" % state)
        log("RADAR_BRIEF_RESULT=skipped agent=%s state=%s" % (AGENT_NAME, state))
        return 0

    # F2 — narrow backstop: normalize the wording ONLY if the model's own
    # reply already shows Vikunja failed this run. A no-op otherwise.
    text = _ensure_gap_line(text)

    log("----- %s (state=%s) -----" % (TITLE, state))
    log(text)
    try:
        code = notify_email("%s — %s" % (TITLE, day), text)
        log("brief emailed via notify-mcp (HTTP %s)" % code)
        log("RADAR_BRIEF_RESULT=sent agent=%s" % AGENT_NAME)
    except Exception as e:
        log("WARN: notify-mcp delivery failed: %r" % e)
        log("RADAR_BRIEF_RESULT=delivery-failed agent=%s" % AGENT_NAME)
    return 0


if __name__ == "__main__":
    sys.exit(main())
