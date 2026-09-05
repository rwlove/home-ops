#!/usr/bin/env python3
# Hermes pre_tool_call gate — the fail-closed security floor for the
# escalation bridge. Wired in config.yaml as a `hooks.pre_tool_call` shell
# hook scoped (matcher) to the `terminal` tool, with `fail_closed: true`.
#
# Contract (Hermes hooks): payload JSON on stdin with `tool_name` + `args`.
# Exit 2 (or {"action":"block"} on stdout) blocks the tool; the block message
# is returned to the model as the tool error. ANY error/timeout here also
# blocks, because the hook entry sets fail_closed: true — so a bug in this
# script fails safe (denies), never opens the gate.
#
# This file is mounted READ-ONLY from a ConfigMap at /opt/hooks/ — physically
# outside the agent-writable /opt/data PVC — so a self-authored skill cannot
# rewrite its own gate (resolves the self-modification escalation).
import json
import re
import sys


def block(msg: str) -> None:
    sys.stdout.write(json.dumps({"action": "block", "message": f"hermes-gate: {msg}"}))
    sys.exit(2)


try:
    payload = json.load(sys.stdin)
except Exception:
    block("unparseable pre_tool_call payload — failing closed")

tool = payload.get("tool_name", "")
args = payload.get("args") or payload.get("tool_input") or {}
# Serialize the whole arg set (command string, workdir, stdin, etc.) to scan.
blob = json.dumps(args, default=str)
cmd = ""
if isinstance(args, dict):
    cmd = str(args.get("command", "")) + " " + str(args.get("input", ""))

# The hook matcher already scopes this to the `terminal` tool, but re-check so
# the script is safe if the matcher is ever widened.
if tool != "terminal":
    sys.exit(0)

# 1. Never allow a permissions bypass on the coding-agent shell-out.
if re.search(r"--dangerously-skip-permissions|--dangerously", cmd):
    block("refused: --dangerously-skip-permissions is never permitted")

# 2. Never let a shell command read or echo credential material (token exfil).
if re.search(r"CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY|sk-ant-", blob):
    block("refused: command references credential material")

# 3. Escalation redaction gate — restricted-tier content must never be
#    serialized to a REMOTE model. Only applies when this terminal call is an
#    escalation to claude (`claude -p ...`); a purely local command that
#    happens to touch a media path stays local and is not redacted here.
is_escalation = bool(re.search(r"\bclaude\b.*(-p|--print)", cmd))
if is_escalation:
    RESTRICTED = [
        r"/mnt/mass_storage",
        r"/mnt/downloads",
        r"/mnt/kubernetes",
        r"kubernetes/apps/media",
        r"kubernetes/apps/security",
        r"\bExternalSecret\b",
        r"op://",  # 1Password refs
        r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}",  # MAC address
    ]
    for pat in RESTRICTED:
        if re.search(pat, blob):
            block(f"escalation redaction: restricted-tier pattern in claude -p context ({pat})")

# Allowed.
sys.exit(0)
