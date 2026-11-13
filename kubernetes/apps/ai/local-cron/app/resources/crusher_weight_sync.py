#!/usr/bin/env python3
"""Daily PUMP->Medikeep weight reconcile trigger (local-cron, kagent tier).

Invokes the in-cluster `crusher` agent over A2A and asks it to run its ONE
guarded write tool, `upsert_weight` (health-mcp-rw), which reconciles PUMP
bodyweight into Medikeep over a rolling 60-day window — adding new weigh-ins and
propagating post-hoc PUMP corrections. The numeric write is deterministic inside
the tool; the agent just invokes it and reports the counts.

PROBLEM/CHANGE-ONLY — NOT a digest. It emails Robert ONLY when the reconcile
made (or, in dry-run, would make) a correction to a historical value, or hit an
error. The routine path — a new daily weigh-in added, or nothing to do — is
SILENT, per the problems-only / surface-on-state-change discipline. A new daily
weigh-in is expected; a changed PAST value is worth a heads-up.

The agent ends its reply with a machine-readable sentinel:
  WEIGHT_SYNC dry_run=<bool> creates=<n> updates=<n> skips=<n> errors=<n>
This script gates the email on updates>0 or errors>0, and fails SAFE (surfaces)
on a missing/garbled sentinel rather than swallowing an unknown outcome. If the
reported error looks like a transient MCP transport blip (a fail-closed ext_proc
stream reset — EOF / connection reset / deadline / 5xx), it retries the reconcile
ONCE before gating; upsert_weight is date-idempotent so the retry can't
double-write. A tool-level logical error is never retried — it surfaces.

Data classification: all reasoning runs on the LOCAL kagent model; every weight
value stays in-cluster. The only thing leaving is the short correction summary
notify-mcp relays to Robert (a Robert-locked private sink). Stdlib only.

Env: AGENT_URL AGENT AGENT_NAME TITLE PROMPT
     [BUDGET_SECONDS=600] [NOTIFY_URL=http://notify-mcp.mcp-system:8060/send]
     [RETRY_BACKOFF_SECONDS=3]
"""
import datetime
import json
import os
import re
import sys
import time
import urllib.request

import a2a_client

AGENT_URL = os.environ["AGENT_URL"]
# The real kagent Agent CR name (namespace `ai`) to invoke via AGENT_URL's MCP
# endpoint — distinct from AGENT_NAME (notify label only).
AGENT = os.environ["AGENT"]
AGENT_NAME = os.environ.get("AGENT_NAME", "crusher")
TITLE = os.environ.get("TITLE", "crusher weight sync")
BUDGET = int(os.environ.get("BUDGET_SECONDS", "600"))
NOTIFY_URL = os.environ.get("NOTIFY_URL", "http://notify-mcp.mcp-system:8060/send")
# Short pause before the single transient-blip retry (see _TRANSIENT_RE).
RETRY_BACKOFF = float(os.environ.get("RETRY_BACKOFF_SECONDS", "3"))

CONTRACT = (
    "\n\nOUTPUT CONTRACT — follow it exactly:\n"
    "- Reply in ENGLISH ONLY. This is a ONE-WAY notification: never ask a "
    "question or request confirmation — nobody can reply.\n"
    "- Call upsert_weight exactly once. NEVER fabricate a count or a value; "
    "report only what the tool returned.\n"
    "- Report ONLY exceptions: list each date the tool UPDATED (a PUMP "
    "correction) as `YYYY-MM-DD: <old> -> <new> lb`, and list any errors "
    "verbatim. Do NOT list newly-created daily weigh-ins and do NOT write an "
    "all-clear line — a routine run with no updates and no errors has no prose.\n"
    "- END your reply with EXACTLY one final line, no matter what:\n"
    "  WEIGHT_SYNC dry_run=<true|false> creates=<n> updates=<n> skips=<n> errors=<n>\n"
    "  using the exact integer counts from the tool's result."
)

SENTINEL_RE = re.compile(
    r"WEIGHT_SYNC\s+dry_run=(\w+)\s+creates=(\d+)\s+updates=(\d+)\s+skips=(\d+)\s+errors=(\d+)",
    re.IGNORECASE,
)

# One retry fires ONLY when the reported error looks like a transient transport
# blip, NEVER on a tool-level logical error. The mcp-gateway broker's ext_proc
# EnvoyFilter runs fail-closed (failure_mode_allow: false), so a sub-second gRPC
# stream reset inside the run window kills the in-flight upsert_weight call and
# the agent reports it verbatim — EOF / connection reset / connection refused /
# deadline exceeded / a 5xx. upsert_weight is create-or-update keyed on the
# reading DATE (never deletes, never adds a second row per date — see health-mcp
# server.py), so re-invoking it is safe under at-least-once delivery:
# already-written dates skip, the dropped date is retried. A logical tool error
# (bad value, 4xx, "not found") matches none of these and is surfaced, not
# retried.
_TRANSIENT_RE = re.compile(
    r"\bEOF\b|ext[_-]?proc|error receiving request|stream reset|rst_stream|"
    r"connection reset|reset by peer|econnreset|broken pipe|"
    r"connection refused|econnrefused|"
    r"context deadline exceeded|deadline exceeded|deadlineexceeded|"
    r"timed out|\btimeout\b|"
    r"internal server error|bad gateway|service unavailable|gateway timeout|"
    r"upstream connect error|\bunavailable\b|\b50[0234]\b",
    re.IGNORECASE,
)


def log(m):
    print(m, flush=True)


def notify_email(subject, body):
    """POST a themed HTML email through notify-mcp's /send route. No auth:
    notify-mcp holds the recipient-lock, so it can only ever email Robert."""
    data = json.dumps({"subject": subject, "body": body, "agent": AGENT_NAME}).encode()
    req = urllib.request.Request(NOTIFY_URL, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.getcode()


def main():
    prompt = os.environ["PROMPT"].strip() + CONTRACT
    day = datetime.date.today().isoformat()

    text, state = a2a_client.call_agent(
        AGENT_URL, AGENT, prompt, budget=BUDGET, message_id="crusher-weight-sync"
    )

    if not text:
        # Unreachable/slow/empty agent: tolerate a skipped run (daily cadence,
        # idempotent reconcile — tomorrow retries). a2a_client never raises, so
        # this degrades to exit 0, never a KubeJobFailed page.
        log("no agent text (state=%s) — silent skipped run; tomorrow retries" % state)
        log("CRUSHER_WEIGHT_SYNC_RESULT=skipped state=%s" % state)
        return 0

    log("----- %s (state=%s) -----" % (TITLE, state))
    log(text)

    m = SENTINEL_RE.search(text)
    if not m:
        # Fail SAFE: a write job whose outcome we cannot parse is surfaced,
        # never silently swallowed.
        log("CRUSHER_WEIGHT_SYNC_RESULT=no-sentinel")
        try:
            notify_email("%s — unparsed result %s" % (TITLE, day), text)
        except Exception as e:  # noqa: BLE001
            log("WARN: notify-mcp delivery failed: %r" % e)
        return 0

    dry_run = m.group(1)
    creates, updates, skips, errors = (int(m.group(i)) for i in (2, 3, 4, 5))
    log(
        "CRUSHER_WEIGHT_SYNC_RESULT=parsed dry_run=%s creates=%d updates=%d skips=%d errors=%d"
        % (dry_run, creates, updates, skips, errors)
    )

    # One bounded retry on a TRANSIENT transport blip (see _TRANSIENT_RE). A
    # fail-closed ext_proc stream reset turns a would-be-silent routine
    # reconcile into an errors>0 email; because upsert_weight is date-idempotent
    # the retry can't double-write, so re-invoking the agent once clears a
    # sub-second blip instead of paging Robert over it. Exactly one retry.
    if errors > 0 and _TRANSIENT_RE.search(text):
        log("transient transport error in result (errors=%d) — one retry in %ss" % (errors, RETRY_BACKOFF))
        log("CRUSHER_WEIGHT_SYNC_RESULT=retry-transient")
        time.sleep(RETRY_BACKOFF)
        rtext, rstate = a2a_client.call_agent(
            AGENT_URL, AGENT, prompt, budget=BUDGET, message_id="crusher-weight-sync-retry"
        )
        rm = SENTINEL_RE.search(rtext) if rtext else None
        if rm:
            # Adopt the retry as authoritative — it supersedes the blipped run.
            log("----- %s retry (state=%s) -----" % (TITLE, rstate))
            log(rtext)
            text = rtext
            dry_run = rm.group(1)
            creates, updates, skips, errors = (int(rm.group(i)) for i in (2, 3, 4, 5))
            log(
                "CRUSHER_WEIGHT_SYNC_RESULT=parsed-after-retry dry_run=%s creates=%d updates=%d skips=%d errors=%d"
                % (dry_run, creates, updates, skips, errors)
            )
        else:
            # Retry gave no parseable result — keep the original and let the
            # email gate surface it. Never swallow an unknown outcome.
            log("retry produced no parseable sentinel (state=%s) — keeping original result"
                % (rstate if rtext else "no-text"))
            log("CRUSHER_WEIGHT_SYNC_RESULT=retry-unparsed")

    # Problems/changes-only: email ONLY on a historical correction or an error.
    # A routine run (new weigh-in added and/or everything already aligned) is
    # silent.
    if updates > 0 or errors > 0:
        try:
            code = notify_email("%s — %s" % (TITLE, day), text)
            log("notified via notify-mcp (HTTP %s)" % code)
            log("CRUSHER_WEIGHT_SYNC_RESULT=sent updates=%d errors=%d" % (updates, errors))
        except Exception as e:  # noqa: BLE001
            log("WARN: notify-mcp delivery failed: %r" % e)
            log("CRUSHER_WEIGHT_SYNC_RESULT=delivery-failed")
    else:
        log("routine run (creates=%d skips=%d, no updates/errors) — silent" % (creates, skips))
        log("CRUSHER_WEIGHT_SYNC_RESULT=silent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
