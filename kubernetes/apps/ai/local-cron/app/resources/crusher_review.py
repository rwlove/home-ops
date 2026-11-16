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

Why `_clean()` exists: the local model (qwen) narrates its tool use inline when it
fumbles a memory write — it emits the digest, then first-person working-narration
("I appended to the wrong entity. Let me clean this up…"), then re-emits the
digest. kagent returns that whole turn as one string, and this runner used to
email it verbatim. `_clean()` reduces the raw reply to the single digest block:
the CONTRACT asks the agent to wrap its final answer in <<<DIGEST>>>…<<<END>>>
markers emitted once (primary path); if it ignores them (it does, under tool-call
pressure) the fallback strips stray markers + narration lines and de-dupes
repeated blocks. Markers never reach the email.

Data classification: ALL medical reasoning runs on the LOCAL kagent model (qwen on
the Spark via LiteLLM) and every reading/dose/weight stays in-cluster. The ONE
thing that leaves the cluster is the short interpretation text in the email
notify-mcp relays to Robert (a Robert-directed private sink). Never a public sink,
never a remote model. Stdlib only (runs on the shared local-cron python image).

The weekly finding is recorded to memory-mcp by THIS runner (see
memory_mcp_client), not by the agent — the local model mis-targeted the entity
and duplicated observations when it owned the write, so the runner now owns the
entity name (a literal) and writes one observation per run, idempotently.

Env: AGENT_URL AGENT AGENT_NAME TITLE PROMPT
     [BUDGET_SECONDS=800] [NOTIFY_URL=http://notify-mcp.mcp-system:8060/send]
     [MEMORY_ENTITY=crusher_weekly_review]
     [MEMORY_MCP_URL=http://memory-mcp.mcp-system.svc.cluster.local:8070/mcp]
"""
import datetime
import json
import os
import re
import sys
import urllib.request

import a2a_client
import memory_mcp_client

AGENT_URL = os.environ["AGENT_URL"]
# The real kagent Agent CR name (namespace `ai`) to invoke via AGENT_URL's MCP
# endpoint — distinct from AGENT_NAME (notify/title label only).
AGENT = os.environ["AGENT"]
AGENT_NAME = os.environ.get("AGENT_NAME", "crusher")
TITLE = os.environ.get("TITLE", "crusher trend review")
BUDGET = int(os.environ.get("BUDGET_SECONDS", "800"))
NOTIFY_URL = os.environ.get("NOTIFY_URL", "http://notify-mcp.mcp-system:8060/send")
# Single, runner-owned memory entity for the weekly finding — a literal, never
# model-chosen (the model mis-targeted/duplicated when it owned the write).
MEMORY_ENTITY = os.environ.get("MEMORY_ENTITY", "crusher_weekly_review")
MEMORY_MCP_URL = os.environ.get(
    "MEMORY_MCP_URL", "http://memory-mcp.mcp-system.svc.cluster.local:8070/mcp")

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
    "- STRUCTURE the reply so it SCANS — never one prose paragraph. Emit, in order:\n"
    "  (a) a one-line **bold** status headline: the single finding and what it means;\n"
    "  (b) a bulleted block, one `- ` bullet per metric, each LEADING with a **bold** "
    "label — **BP** (trailing trend), **Weight** (weight<->BP relationship), **Meds** "
    "(any line-up with a dose change), **Screening** (due items). Omit a bullet whose "
    "metric has nothing new to report; do NOT write the labels (a)/(b)/(c) in the reply;\n"
    "  (c) a final `### Worth raising` heading, then the one line to raise at the next "
    "visit.\n"
    "- Keep the WHOLE reply under 900 characters. Use **bold**, `- bullets`, `code`, and "
    "short `#`/`##`/`###` headings — no tables, no images; it is rendered into a themed "
    "HTML email.\n"
    "- Do NOT include any of your working in the reply: no tool-call commentary, no "
    "self-correction, no notes about what you saved or which memory entity you wrote "
    "to. Only the finished interpretation — write it once, do not repeat it.\n"
    "- Wrap your ENTIRE final reply between a line containing exactly <<<DIGEST>>> "
    "and a line containing exactly <<<END>>>, output that block EXACTLY ONCE, and "
    "put nothing before or after the markers.\n"
    "- Do NOT call any notify/alert/email tool — THIS job delivers your reply. Just "
    "return the interpretation as your answer."
)

# The agent wraps its final digest between these markers (the CONTRACT asks for
# it). We strip them before emailing — they never reach the email body.
_DIGEST_START = "<<<DIGEST>>>"
_DIGEST_END = "<<<END>>>"

# First-person working-narration the local model leaks between tool calls
# ("I see the observation was appended to the wrong entity. Let me clean this
# up…"). Only stripped on the FALLBACK path — when the agent ignored the markers,
# so it has already misbehaved and best-effort cleanup beats emailing the raw turn.
_NARRATION_RE = re.compile(
    r"^\s*(?:I|I'm|I've|I'll|I'd|Let me|Let's|Now|Okay|OK|Wait|Actually|First|Next|"
    r"Then|Hmm|So I|Looking|Checking|Searching|Appending|Adding|Creating)\b",
    re.IGNORECASE,
)


def log(m):
    print(m, flush=True)


def _dedupe_blocks(text):
    """Collapse exactly-repeated paragraphs (blank-line separated), preserving
    first-occurrence order. Catches the model emitting the same block twice."""
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
    """Reduce the agent's raw reply to the single digest block we email.

    Primary: take the LAST complete <<<DIGEST>>>…<<<END>>> block (robust to the
    model re-emitting it) and strip the markers.

    Fallback (markers absent — the local qwen does this under tool-call pressure):
    drop stray marker lines, strip first-person working-narration lines, and
    de-duplicate repeated blocks. Never returns empty for non-empty input.
    """
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
    holds the recipient-lock, so it can only ever email Robert."""
    data = json.dumps({"subject": subject, "body": body, "agent": AGENT_NAME}).encode()
    req = urllib.request.Request(NOTIFY_URL, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.getcode()


def main():
    prompt = os.environ["PROMPT"].strip() + CONTRACT
    day = datetime.date.today().isoformat()

    text, state = a2a_client.call_agent(AGENT_URL, AGENT, prompt, budget=BUDGET, message_id="crusher-review")
    # Strip the agent's working-narration / duplicate blocks down to the digest
    # before anything downstream sees it (see module docstring + _clean).
    text = _clean(text)

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

    # Record the week's finding deterministically — the RUNNER owns the entity
    # name and the write, not the model (which mis-targeted/duplicated). One
    # observation per run, idempotent per day. Non-fatal: the email already went.
    try:
        ok, detail = memory_mcp_client.write_weekly_observation(
            MEMORY_ENTITY, text, day, mcp_url=MEMORY_MCP_URL)
        log("memory write %s on '%s': %s" % ("OK" if ok else "FAILED", MEMORY_ENTITY, detail))
        log("CRUSHER_REVIEW_MEMORY=%s" % ("ok" if ok else "failed"))
    except Exception as e:
        log("WARN: memory-mcp write failed: %r" % e)
        log("CRUSHER_REVIEW_MEMORY=error")
    return 0


if __name__ == "__main__":
    sys.exit(main())
