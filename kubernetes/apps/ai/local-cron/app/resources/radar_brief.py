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
Robert wants every day, so it delivers the agent's reply whenever the invoke
actually COMPLETED — including a completed reply that itself reports a
source as unreachable (e.g. "Vikunja unreachable"), which is still a real,
useful partial brief (see `_ensure_gap_line` below). It stays silent
(a2a_client never raises -> a silent skipped run, tomorrow retries, no page)
only when the agent returns no text at all. A TOTAL invoke failure that DOES
return text (task state != "completed" — e.g. the kagent runtime's "input
was not accepted; retry after the session becomes available") is an infra
failure, not a brief: emailing the raw error as a "morning brief" is inbox
junk posing as a digest, so F4 (in `main()`) suppresses that email and fails
the Job instead, surfacing through normal kagent cron-failure alerting where
infra failures belong.

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
from english_gate import is_non_english

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
    "- LINE FORMAT for every Today/This-week line: `<time-or-weekday> — <item> "
    "[· calendar] · <owner>`. Content leads; source and owner trail:\n"
    "  * The calendar event summary or Vikunja task title carries a leading "
    "`[Persona]` tag by convention (e.g. `[Crusher]`, `[storage-operator]`, case-"
    "insensitive). STRIP that tag from the item text and move it to the END of "
    "the line, lowercased, as the owner (e.g. `crusher`, `storage-operator`, "
    "`vila`). If an item has no `[Persona]` tag, omit the trailing owner "
    "entirely — never invent one.\n"
    "  * A calendar event carries the literal word `calendar` before the owner. "
    "A Vikunja task carries NO source word at all — never write `vikunja` on a "
    "line.\n"
    "  * In `### Today`: a calendar event's `<time-or-weekday>` is its TIME "
    "ONLY, `HH:MM ET` (never a date — it's today). A Vikunja task due exactly "
    "today leads with the word `today`; an OVERDUE Vikunja task leads with the "
    "word `overdue`; a fallback task (used ONLY when nothing is due today or "
    "overdue) has no leading time token at all — start directly with the item.\n"
    "  * In `### This week`: every line (calendar or Vikunja) leads with the "
    "WEEKDAY LABEL ONLY, copied verbatim from the weekday map you were given "
    "(e.g. `Wednesday`, `next Monday`) — never a time, never a numeric date.\n"
    "  * Examples: `11:30 ET — PT — ATI Physical Therapy · calendar · "
    "crusher`, `overdue — Replace HVAC filter (P3) · vila`, "
    "`Wednesday — Check image-GC report: ~/image-gc-check-latest.txt · "
    "calendar · storage-operator`.\n"
    "- STRUCTURE the reply so it SCANS — never one prose paragraph. Emit, in order:\n"
    "  (a) a `### Today` heading. List Vikunja tasks due today or overdue FIRST, "
    "ranked by priority P5 to P1 then soonest due date, flagging any `robert-action` "
    "label; if NOTHING is due today or overdue, fall back to the top 3-5 highest-"
    "priority OPEN tasks instead so this section is never empty. Then list today's "
    "calendar events.\n"
    "  (b) a `### This week` heading, ordered CHRONOLOGICALLY. It covers TOMORROW "
    "onward only — anything dated today belongs under Today and must NEVER appear "
    "here too. List Vikunja tasks due in that window (same P5-to-P1 then soonest-"
    "due ranking, excluding anything already listed under Today) together with "
    "calendar events in that window. Omit this heading entirely if nothing "
    "qualifies.\n"
    "  (c) a final `### Open PRs awaiting you` heading — a short, PLAIN, UNRANKED "
    "list of open home-ops PRs (not folded into Today/This week, no trailing "
    "owner on these lines). Note each PR's CI state if the tool surfaces it; "
    "otherwise say 'CI state not available' rather than guessing green/red. Omit "
    "this heading if there are no open PRs.\n"
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


def awaiting_review_line():
    """One deterministic line: how many Fleet Todos were completed since
    REVIEW_SINCE without the `followup-reviewed` label, i.e. still owed the
    Claude-persona follow-up review that runs in a laptop session (whats-next).
    The RUNNER computes this from Vikunja; the model never counts it. Returns ""
    when nothing is waiting, when Vikunja isn't configured, or on any error
    (logged), so the brief never fails over this line."""
    url = os.environ.get("VIKUNJA_URL", "").rstrip("/")
    token = os.environ.get("VIKUNJA_TOKEN", "")
    since = os.environ.get("REVIEW_SINCE", "")
    if not (url and token and since):
        return ""
    import json
    import urllib.request
    try:
        cutoff = datetime.datetime.fromisoformat(since.replace("Z", "+00:00"))

        def get(path):
            req = urllib.request.Request(url + path, headers={"Authorization": "Bearer " + token})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode() or "null")

        pid = next(p["id"] for p in get("/projects?per_page=50") if p.get("title") == "Fleet Todos")
        count, page = 0, 1
        while True:
            chunk = get("/projects/%d/tasks?page=%d&per_page=50" % (pid, page)) or []
            for t in chunk:
                done_at = t.get("done_at") or ""
                if not t.get("done") or not done_at or done_at.startswith("0001"):
                    continue
                if datetime.datetime.fromisoformat(done_at.replace("Z", "+00:00")) < cutoff:
                    continue
                if "followup-reviewed" in {lb.get("title") for lb in (t.get("labels") or [])}:
                    continue
                count += 1
            if len(chunk) < 50:
                break
            page += 1
    except Exception as e:
        log("WARN: awaiting-review count failed: %r" % e)
        return ""
    log("RADAR_BRIEF_AWAITING_REVIEW=%d" % count)
    if not count:
        return ""
    return ("**Completed todos awaiting follow-up review:** %d (run `whats-next` "
            "in a Claude session)" % count)


def _week_label(today, d):
    """This-week label for date d: the bare weekday name, or "next <Weekday>"
    for the day 7 out that shares today's weekday name."""
    name = d.strftime("%A")
    return "next " + name if (d - today).days >= 7 else name


_SECTION_RE = re.compile(r"^\s*#{1,6}\s*(.+?)\s*$")
_VIKUNJA_SRC_RE = re.compile(r"\s*·\s*vikunja\b", re.IGNORECASE)


def _tidy_lines(text, today):
    """Deterministic backstop for two format rules a weak model drifts on:
    drop the `· vikunja` source tag (todos need no source word), and drop any
    This-week line led by TODAY's bare weekday name — This week starts
    tomorrow, so such a line can only be a repeat of a Today item (the
    same-weekday day a week out is labelled "next <Weekday>")."""
    if not text:
        return text
    today_name = today.strftime("%A").lower()
    out, section = [], ""
    for ln in text.splitlines():
        m = _SECTION_RE.match(ln)
        if m:
            section = m.group(1).strip("*_ ").lower()
        elif section == "this week":
            lead = ln.strip().lstrip("-*• ").strip("*_ ").lower()
            if lead.startswith(today_name):
                log("WARN: dropped This-week line dated today: %r" % ln)
                continue
        out.append(_VIKUNJA_SRC_RE.sub("", ln))
    # A This-week heading left with no lines under it goes too.
    tidied = "\n".join(out)
    tidied = re.sub(r"(?im)^[ \t]*#{1,6}[ \t]*this week[ \t]*\n(?:[ \t]*\n)*(?=[ \t]*#{1,6}\s|\Z)", "", tidied)
    return tidied.strip()


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
    # and Vikunja due-dates became the lead content. The weekday-name map is
    # the same determinism move applied to "This week"'s weekday-only line
    # format — a weak local model asked to derive "what day of the week is
    # 2026-11-19" from an ISO date will sometimes get it wrong; handing it
    # the precomputed mapping removes that failure mode entirely.
    today = datetime.date.today()
    # "This week" starts TOMORROW: anything dated today is Today-only. The
    # window still reaches today+7, whose weekday collides with today's, so
    # that day is labelled "next <Weekday>" — otherwise next Monday's
    # recurring event reads as a duplicate of today's on a Monday brief.
    window_start = today + datetime.timedelta(days=1)
    window_end = today + datetime.timedelta(days=7)
    weekday_map = "; ".join(
        "%s=%s" % (d.isoformat(), _week_label(today, d))
        for d in (today + datetime.timedelta(days=i) for i in range(1, 8))
    )
    date_context = (
        "Today's actual date is %s (America/New_York); today's items go under "
        "Today ONLY. Treat %s through %s inclusive as the 'This week' window "
        "for both calendar events and Vikunja task due dates. Do NOT assume, "
        "guess, or carry over any other date — use exactly this one.\n\n"
        "Weekday labels for this window (America/New_York), precomputed — use "
        "ONLY these, verbatim, for the 'This week' section's labels, never "
        "compute or guess one yourself: %s\n\n"
        % (today.isoformat(), window_start.isoformat(), window_end.isoformat(), weekday_map)
    )
    prompt = date_context + os.environ["PROMPT"].strip() + CONTRACT
    day = today.isoformat()

    text, state = a2a_client.call_agent(AGENT_URL, AGENT, prompt, budget=BUDGET, message_id="radar-brief")
    text = _clean(text)
    text = _tidy_lines(text, today)

    if not text:
        log("no agent text (state=%s) — silent skipped run; tomorrow retries" % state)
        log("RADAR_BRIEF_RESULT=skipped agent=%s state=%s" % (AGENT_NAME, state))
        return 0

    # F4 — a TOTAL invoke failure (the task never reached a completed state)
    # is an infra failure, not a brief: emailing the raw error text as a
    # "morning brief" is inbox junk posing as a digest. Gate strictly on the
    # task STATE, never on string-matching the text — a `state=completed`
    # reply that itself reports a source as unreachable (the F2 path right
    # below) is still a real, useful partial brief and MUST still email.
    # Only a non-"completed" state (failed/transport-error/canceled/...)
    # means the invoke itself never finished; suppress the email and fail
    # the Job so it surfaces through normal kagent cron-failure alerting
    # instead of a silent skip (the `not text` branch above) OR a bogus
    # "brief".
    if state != "completed":
        log("invoke did not complete (state=%s) — suppressing email, failing the Job" % state)
        log("reply text (NOT emailed): %s" % text)
        log("RADAR_BRIEF_RESULT=invoke-failed agent=%s state=%s" % (AGENT_NAME, state))
        return 1

    # F2 — narrow backstop: normalize the wording ONLY if the model's own
    # reply already shows Vikunja failed this run. A no-op otherwise.
    text = _ensure_gap_line(text)

    # English-only send-gate (shared english_gate; home-ops#15899). A CJK-drifted brief
    # is unreadable to Rob; suppress it as a silent skipped run (the daily cadence
    # retries). An adherence miss, not an infra failure — return 0, never page.
    if is_non_english(text):
        log("non-English reply (CJK detected) — English-only contract violated; "
            "suppressing brief (silent skipped run)")
        log("RADAR_BRIEF_RESULT=skipped-non-english agent=%s state=%s" % (AGENT_NAME, state))
        return 0

    review = awaiting_review_line()
    if review:
        text = text.rstrip() + "\n\n" + review

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
