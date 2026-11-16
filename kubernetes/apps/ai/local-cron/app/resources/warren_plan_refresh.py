#!/usr/bin/env python3
"""warren monthly retirement-plan refresh (local-cron, kagent tier).

Invokes the in-cluster `warren` financial-advisor kagent agent over the shared,
tested a2a_client (kagent-controller.ai:8083/mcp, invoke_agent) and asks it to
re-pull the live portfolio + finance model and RE-SOLVE the retirement plan with
Owl, PERSISTING the refreshed plan into the finance memory namespace itself.

Replaces the inline `curl | jq` raw-A2A glue that called the dead kagent-1.0
per-agent endpoint (`http://warren.ai:8080/`), removed when kagent's ATE
rewrite retired per-agent Services -- see a2a_client's module docstring. Moving
onto a2a_client also drops a FIXED `messageId` ("warren-plan-refresh") that
pinned an eternal stale Session; a2a_client appends a per-run nonce instead.

Data classification (unchanged from the prior script, strictly preserved):
everything warren touches is RESTRICTED. All figures (balances, spend,
lifetime totals, conversion amounts) are written by the agent ITSELF into
memory-mcp (finance namespace) -- the designated in-cluster Restricted store.
This runner sends NO Pushover and NO email, and ECHOES ONLY a small fixed
status vocabulary to the log (never warren's raw reply text) because this
job's stdout ships to Loki (indexed) -- belt-and-suspenders against a figure
leaking into Loki even if warren itself breaks its own figure-free contract.

Persist-first: warren's system prompt writes the result entity + inputs FIRST,
then solves, then appends outputs -- so even a timeout or transport failure
here may still have a saved plan; check the finance memory namespace before
assuming data loss from this job's exit code. A non-"REFRESHED ok" exit is the
correct, VISIBLE last-run signal (KubeJobFailed) -- this job deliberately does
NOT degrade failures to a silent skip the way the digest-style crons do.

Env: AGENT_URL AGENT AGENT_NAME PROMPT [BUDGET_SECONDS=1500]
"""
import os
import sys

import a2a_client

AGENT_URL = os.environ["AGENT_URL"]
# The real kagent Agent CR name (namespace `ai`) to invoke via AGENT_URL's MCP
# endpoint -- distinct from AGENT_NAME (log label only).
AGENT = os.environ["AGENT"]
AGENT_NAME = os.environ.get("AGENT_NAME", "warren")
# < the job's activeDeadlineSeconds (1800) so this client-side timeout fires
# first, matching the old curl --max-time 1500 ceiling.
BUDGET = int(os.environ.get("BUDGET_SECONDS", "1500"))


def log(m):
    print(m, flush=True)


def main():
    prompt = os.environ["PROMPT"].strip()

    text, state = a2a_client.call_agent(
        AGENT_URL, AGENT, prompt, budget=BUDGET, message_id="warren-plan-refresh")

    t = (text or "").strip()

    # Reply MUST be figure-free by contract; guard anyway. Only a small
    # allowlisted status vocabulary is ever echoed to the log, so a
    # misbehaving reply can never leak a balance/figure into Loki.
    if "REFRESHED ok" in t:
        log("warren-plan-refresh: REFRESHED ok (state=%s)" % state)
        log("WARREN_REFRESH_RESULT=ok agent=%s" % AGENT_NAME)
        return 0

    if "ERROR:" in t:
        # Echo only the fixed prefix, not warren's free text, to keep any
        # stray figures out of the log.
        log("warren-plan-refresh: warren reported ERROR (state=%s) -- check "
            "the finance memory namespace" % state)
        log("WARREN_REFRESH_RESULT=error agent=%s" % AGENT_NAME)
        return 1

    if not t:
        log("warren-plan-refresh: empty reply (state=%s) -- likely persisted "
            "via persist-first; check memory" % state)
        log("WARREN_REFRESH_RESULT=empty agent=%s" % AGENT_NAME)
        return 1

    log("warren-plan-refresh: unexpected non-token reply (state=%s) -- not "
        "echoed (may contain figures); check memory" % state)
    log("WARREN_REFRESH_RESULT=unexpected agent=%s" % AGENT_NAME)
    return 1


if __name__ == "__main__":
    sys.exit(main())
