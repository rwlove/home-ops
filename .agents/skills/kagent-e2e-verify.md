---
name: kagent-e2e-verify
description: Prove a kagent Agent actually completes a task (LLM + MCP tool) after any kagent infra/agent/content change
---

# kagent e2e verify

A kagent Agent's `status` conditions (`Ready`, `EgressPoliciesApplied`,
`ResolvedRefs`, …) can all read `True` while the Agent is completely
unable to do anything — the controller's self-reported status does
not confirm the underlying Substrate credential grant or network path
actually works end to end. This happened cluster-wide after the
kagent 1.0 ATE cutover: every Agent showed healthy status while every
real invocation failed `403 Forbidden` (home-ops#15855). **CI green
and `Ready: True` are not proof.** This skill is the proof.

## When to use

Run this after ANY of:

- A kagent/ATE Substrate infra change (`ai/kagent`, `ate-system/substrate`,
  an `EgressPolicy`/`WorkerPool`/`Harness` CR, a CiliumNetworkPolicy
  touching the `ai` namespace or `ate-system`).
- A new or edited `Agent`/`AgentTemplate` CR (model config, tool
  wiring, `egressPolicyRefs`, system prompt).
- A `ModelConfig` or `RemoteMCPServer` change (litellm, mcp-gateway,
  the built-in `kagent-tool-server`).
- Before merging a PR that re-points an agent-invocation consumer
  (local-cron, `alert-agent-bridge`, `agent-callback`, or any future
  caller) at a new URL/protocol.

This is the real merge bar for all of the above — a green
`kubectl kustomize` build and a passing CI render say nothing about
whether the Agent can actually call its model or its tools.

## What "proof" means here

Not a 200 status code. Not `Ready: True`. The task must reach
`TASK_STATE_COMPLETED` (or MCP's lowercased `completed`) AND the run
must demonstrably have touched **both**:

1. **The LLM leg** — a real completion through litellm (check
   `kagent.dev/a2a/usage` / `structuredContent` token counts are
   non-zero in the raw response, or just trust a sensible reply).
2. **The MCP-tool leg** — a real tool call and tool response (ask the
   agent to call a cheap, harmless tool it already has, e.g.
   `k8s_get_available_api_resources` for any read-only operator, and
   confirm the response contains real tool output, not just the LLM's
   own text).

A task that reaches `TASK_STATE_FAILED` — even with a *plausible*
error message — is a FAIL. Don't rationalize a failure as "probably
fine." Don't stop at the first agent that works if the change is
fleet-wide (CNP/EgressPolicy/Substrate changes affect every Agent) —
spot-check at least two independent Agents.

## Recipe

Pick a cheap, read-only canary agent (`ml-operator` is a good
default: Green tier, two built-in tools, no destructive path). Run
from a disposable, non-GitOps debug pod in the `ai` namespace —
nothing here is itself a GitOps artifact, it's purely a live probe.

```sh
# 1. Spin up a throwaway pod in `ai` (same namespace as kagent-controller
#    and every egress destination — the baseline allow-intra-namespace
#    CNP already permits this without any extra policy).
kubectl run kagent-e2e-probe -n ai --image=docker.io/library/python:3.15-slim \
  --restart=Never -- sleep 300
kubectl -n ai wait --for=condition=Ready pod/kagent-e2e-probe --timeout=60s

# 2. Invoke the canary agent via the controller's raw A2A endpoint
#    (/agents/<namespace>/<name>, method SendMessage, role ROLE_USER,
#    header A2A-Version: 1.0 — kagent-dev/kagent#1160). This is the SAME
#    underlying surface the MCP tools/call->invoke_agent wrapper local-cron
#    and friends use, but used directly here because it's the only shape
#    that exposes the task's `artifacts` — the structural evidence a tool
#    was actually called. (The MCP wrapper collapses everything down to
#    final text, which makes the proof dependent on the model choosing to
#    echo back a magic word — confirmed flaky in practice: the SAME prompt
#    against the SAME agent sometimes hallucinates "I don't have that
#    tool" even when it does. Checking for a real function_call artifact
#    is deterministic where text-matching is not.)
#
#    Prompt deliberately exercises BOTH legs: a real tool call, then a
#    real LLM completion. Retries once — one flaky "the model didn't
#    bother calling the tool this time" is a model quirk, not an infra
#    failure; two in a row is a real signal.
kubectl -n ai exec kagent-e2e-probe -- python3 -c '
import json, urllib.request

AGENT = "ml-operator"

def invoke(msg_id):
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "SendMessage",
        "params": {"message": {
            "role": "ROLE_USER",
            "parts": [{"kind": "text", "text": (
                "Use your k8s_get_available_api_resources tool once, then "
                "reply with exactly the single word DONE and nothing else.")}],
            "messageId": msg_id, "kind": "message",
        }},
    }
    req = urllib.request.Request(
        "http://kagent-controller.ai:8083/agents/ai/%s" % AGENT,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "A2A-Version": "1.0"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())

ok = False
for attempt in range(2):
    task = invoke("e2e-verify-%d" % attempt).get("result", {}).get("task", {})
    state = (task.get("status", {}) or {}).get("state", "")
    tool_called = any(
        (p.get("metadata", {}) or {}).get("kagent.dev/a2a/part-type") == "function_call"
        for art in (task.get("artifacts") or []) for p in (art.get("parts") or []))
    print("attempt", attempt, "state=", state, "tool_called=", tool_called)
    if state == "TASK_STATE_COMPLETED" and tool_called:
        ok = True
        break
assert ok, "FAIL — task never reached TASK_STATE_COMPLETED with a real tool call"
print("PASS")
'

# 3. Clean up — this probe is not GitOps-managed, nothing to revert.
kubectl delete pod -n ai kagent-e2e-probe --wait=false
```

If step 2 raises the `assert` or prints anything other than `PASS`,
the change under test is **not proven** — do not merge the PR it's
validating. Re-run against a second agent (swap `AGENT`) before
trusting a fleet-wide infra change; a single agent passing doesn't
rule out a per-agent `egressPolicyRefs` gap.

### Interactive alternative

If you're a Claude Code session with the kagent-agents MCP tools bound
(`mcp__kagent-agents__list_agents` /
`mcp__kagent-agents__invoke_agent`), those call the identical
controller endpoint and are fine for a quick sanity check — but they
only return final text, not the tool-call artifacts, so they can't
make the structural "a tool was really called" assertion above. Use
them to confirm an agent responds at all; use the recipe above (or the
raw A2A endpoint directly) for the actual proof.

## Where to look when it fails

- **403 / credential-provider denial** → `EgressPolicy` CR missing or
  not referenced from the Agent's `egressPolicyRefs`, or the
  Substrate's `credentialProvider.namespacePolicies` (ate-system/
  substrate HelmRelease) doesn't grant the atespace. Check
  `kubectl -n ate-system logs deploy/k8s-credential-provider`.
- **503 / "Connect: deadline has elapsed"** → a CiliumNetworkPolicy
  gap: the destination Service has no ingress rule admitting
  `ate-system`'s `atenet-egress` (the SNAT hop every Actor's egress
  rides through). Check `kubectl -n ate-system logs deploy/atenet-egress`.
- **Transport error / DNS failure reaching the controller itself** →
  the caller is still pointed at a stale per-agent URL
  (`http://<agent>.ai:8080/`) from the pre-1.0 ATE model. Re-point at
  `http://kagent-controller.ai:8083/mcp`.
- **Task completes but never calls the tool** → prompt engineering, not
  infra — not what this skill checks for.

## What this is NOT

- Not a substitute for `pre-submit.md`'s CI-facing checks (schema,
  lint, `kustomize build`). Run both.
- Not a full fleet health check or a dashboard. It proves ONE thing —
  that an Agent can complete a real task — not latency, cost, or model
  quality.
- Not a reason to skip `warden` review on an EgressPolicy / security-
  boundary change. This is the functional proof; warden is the
  adversarial review of the grant's scope.
- The scheduled dead-man's-switch alert (last-successful-completion,
  so a future break pages instead of going silent for 18h+) is a
  separate, NOT-YET-BUILT observability follow-up — scope it to
  `observability-operator`, don't fold it into this skill.
