#!/usr/bin/env python3
"""Daily Radar morning-brief digest (local-cron, kagent tier).

Invokes the in-cluster `radar` chief-of-staff agent over A2A (via the shared,
poll-to-terminal a2a_client) and asks it to assemble Robert's morning brief
from already-grounded, structured records ONLY — today's concierge-calendar
events and open home-ops PRs (CI state) — then delivers the brief to Robert as
a themed HTML email via notify-mcp (`/send`), the same private sink the
sibling local-cron digests use. notify-mcp holds the relay recipient-lock, so
this can only ever email Robert.

Why this is a digest runner, NOT triage_run.py: triage_run is a problem-only
gate (PROBLEM: -> notify, ALL CLEAR -> silent). A morning brief is a DIGEST
Robert wants every day, so this ALWAYS delivers the agent's reply. It only
stays silent when the agent is unreachable or returns nothing (a2a_client
never raises -> a silent skipped run, tomorrow retries, never a
KubeJobFailed page).

Known in-cluster gap (as of authoring): there is no MCP backend federated for
the Vikunja Fleet Todos store (the laptop `whats-next` skill's Source 3) or
for local plan files (Source 2) — see agent-radar.yaml's header comment. The
agent's own systemPrompt instructs it to say so by name rather than invent or
omit the gap; this runner does not try to paper over it.

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
# gather/rank/known-gap instructions, so this PROMPT is deliberately short.
CONTRACT = (
    "\n\nOUTPUT CONTRACT — follow it exactly, it is as important as the task:\n"
    "- Reply in ENGLISH ONLY. This is a ONE-WAY notification: never ask a question "
    "or request input or confirmation — nobody can reply. If unsure, state your "
    "best assessment and proceed.\n"
    "- NEVER invent a date, a todo, a PR, or a finding; every line must come from "
    "a tool call you actually made. If a source errors or returns nothing where "
    "you expected data, say so by name — never omit the gap silently, never "
    "fill it with a guess.\n"
    "- Keep the WHOLE reply under 900 characters. Light markdown is fine "
    "(**bold**, `code`, `- bullets`, short headings) — no tables, no images; it "
    "is rendered into a themed HTML email.\n"
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
    prompt = os.environ["PROMPT"].strip() + CONTRACT
    day = datetime.date.today().isoformat()

    text, state = a2a_client.call_agent(AGENT_URL, AGENT, prompt, budget=BUDGET, message_id="radar-brief")
    text = _clean(text)

    if not text:
        log("no agent text (state=%s) — silent skipped run; tomorrow retries" % state)
        log("RADAR_BRIEF_RESULT=skipped agent=%s state=%s" % (AGENT_NAME, state))
        return 0

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
