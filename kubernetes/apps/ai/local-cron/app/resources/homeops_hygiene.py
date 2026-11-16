#!/usr/bin/env python3
"""homeops-operator weekly repo-hygiene digest (local-cron, kagent tier).

Invokes the in-cluster `homeops-operator` kagent agent over the shared, tested
a2a_client (kagent-controller.ai:8083/mcp, invoke_agent) and asks it to sweep the
home-ops repo + live cluster for hygiene issues (see the cron's PROMPT for the
five areas), then delivers the ranked digest to Rob as a themed HTML email via
notify-mcp (/send) -- the same private, recipient-locked sink the sibling
local-cron digests use.

Replaces the inline `curl | jq` raw-A2A glue that called the dead kagent-1.0
per-agent endpoint (`http://homeops-operator.ai:8080/`), removed when kagent's
ATE rewrite retired per-agent Services. See a2a_client's module docstring for
why: the only invocation surface left is the controller's own MCP endpoint.
Moving onto a2a_client also fixes a second bug this job carried independently
of the dead endpoint -- a FIXED `messageId` ("homeops-hygiene-cron") pins an
eternal stale Session once one exists; a2a_client appends a per-run nonce so
every weekly run gets a fresh Session that resolves current config.

This is NOT triage_run.py: this job's own prompt is the complete output
contract (format, ALL CLEAR sentinel, the FYI: tag) -- it does not share
triage_run's PROBLEM:/ALL CLEAR vocabulary, so this runner owns a small gate of
its own rather than stacking a second, conflicting contract on top. Mirrors the
shape of arnold_queue_check.py / crusher_review.py (dedicated gate + runner-
owned email send), not the generic triage harness.

Gate preserved exactly from the prior inline script:
  ALL CLEAR (anywhere in the reply)        -> silent, nothing sent
  FYI: (anywhere, and not ALL CLEAR)       -> one themed HTML email
  anything else (untagged / malformed)     -> silent, logged, never emailed

Data classification: findings are Internal-tier repo + live-cluster state
(public repo paths, apiVersions, schema status) -- notify-mcp's recipient-lock
to Rob is the only sink.

Env: AGENT_URL AGENT AGENT_NAME PROMPT
     [TITLE=homeops hygiene] [BUDGET_SECONDS=1700]
     [NOTIFY_URL=http://notify-mcp.mcp-system:8060/send]
"""
import datetime
import json
import os
import sys
import urllib.request

import a2a_client

AGENT_URL = os.environ["AGENT_URL"]
# The real kagent Agent CR name (namespace `ai`) to invoke via AGENT_URL's MCP
# endpoint -- distinct from AGENT_NAME (notify/title label only).
AGENT = os.environ["AGENT"]
AGENT_NAME = os.environ.get("AGENT_NAME", "homeops-operator")
TITLE = os.environ.get("TITLE", "homeops hygiene")
# < the job's activeDeadlineSeconds (1800) so this client-side timeout fires
# first and exits cleanly, matching the old curl --max-time 1700 ceiling.
BUDGET = int(os.environ.get("BUDGET_SECONDS", "1700"))
NOTIFY_URL = os.environ.get("NOTIFY_URL", "http://notify-mcp.mcp-system:8060/send")


def log(m):
    print(m, flush=True)


def notify_email(subject, body):
    """POST a themed HTML email through notify-mcp's /send route. No auth: notify-mcp
    holds the recipient-lock, so this can only ever email Rob."""
    data = json.dumps({"subject": subject, "body": body, "agent": AGENT_NAME}).encode()
    req = urllib.request.Request(NOTIFY_URL, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.getcode()


def main():
    prompt = os.environ["PROMPT"].strip()
    day = datetime.date.today().isoformat()

    text, state = a2a_client.call_agent(
        AGENT_URL, AGENT, prompt, budget=BUDGET, message_id="homeops-hygiene")

    # Unreachable / slow / empty: stay silent (weekly cadence tolerates a skip,
    # same convention as crusher_review.py / arnold_queue_check.py). a2a_client
    # never raises, so this degrades to exit 0, never a KubeJobFailed page.
    if not text:
        log("no agent text (state=%s) -- silent skipped run; next week retries" % state)
        log("HYGIENE_RESULT=skipped agent=%s state=%s" % (AGENT_NAME, state))
        return 0

    log("----- %s (state=%s) -----" % (TITLE, state))
    log(text)

    t = text.strip()

    # ALL CLEAR -> silent; checked before the FYI: gate (same precedence as the
    # original inline script).
    if "ALL CLEAR" in t:
        log("ALL CLEAR -- nothing sent")
        log("HYGIENE_RESULT=allclear agent=%s" % AGENT_NAME)
        return 0

    # Positive gate (inbox-spam fix): only a well-formed FYI: digest may reach
    # the mail sink. Anything else -- untagged local-model garbage, a
    # wrong-language reply, a tool-init error string ("failed to extract
    # tools" / "session not found") -- is malformed; log a snippet and exit
    # clean so a bad run can NEVER notify Rob.
    if "FYI:" not in t:
        log("no FYI: tag -- malformed agent output, not notifying")
        log(t[:300])
        log("HYGIENE_RESULT=malformed agent=%s" % AGENT_NAME)
        return 0

    # 3800-char cap keeps the email terse (same ceiling as the original).
    msg = t[:3800]
    try:
        code = notify_email("%s -- %s" % (TITLE, day), msg)
        log("themed FYI digest emailed (HTTP %s)" % code)
        log("HYGIENE_RESULT=sent agent=%s" % AGENT_NAME)
    except Exception as e:  # noqa: BLE001
        log("WARN: notify-mcp delivery failed: %r" % e)
        log("HYGIENE_RESULT=delivery-failed agent=%s" % AGENT_NAME)
    return 0


if __name__ == "__main__":
    sys.exit(main())
