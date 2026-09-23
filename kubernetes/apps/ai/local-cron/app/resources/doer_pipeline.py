#!/usr/bin/env python3
"""Deterministic doer pipeline (write-gate tier).

The LOCAL-MODEL AGENT never holds a credential. This non-LLM step:
  1. asks the read-only kagent agent to triage AND, if a fix is PR-able, emit a
     small structured INTENT proposal (fenced JSON);
  2. lenient-parses + STRICTLY validates that proposal (schema + path allowlist +
     action allowlist);
  3. deterministically applies the intent to a fresh clone (the PIPELINE writes the
     diff, never the model), opens a PR with a scoped token, and NEVER merges.
Anything invalid/absent/no-op degrades to a Pushover of the human-readable triage.
A flaky 7B can only ever cause a PR you decline — never a bad merge, never a
cluster write.

Env: AGENT_URL AGENT_NAME ALLOWED_ACTIONS REPO GITHUB_PR_TOKEN PUSHOVER_TOKEN
     PUSHOVER_USER  (PATH_PREFIX GIT_USER_NAME GIT_USER_EMAIL optional)
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

AGENT_URL = os.environ["AGENT_URL"]
AGENT_NAME = os.environ.get("AGENT_NAME", "doer")
ALLOWED_ACTIONS = set(a.strip() for a in os.environ.get("ALLOWED_ACTIONS", "").split(",") if a.strip())
REPO = os.environ["REPO"]
TOKEN = os.environ.get("GITHUB_PR_TOKEN", "")
PUSH_TOKEN = os.environ.get("PUSHOVER_TOKEN", "")
PUSH_USER = os.environ.get("PUSHOVER_USER", "")
PATH_PREFIX = os.environ.get("PATH_PREFIX", "kubernetes/apps/")
GIT_NAME = os.environ.get("GIT_USER_NAME", "kagent-doer")
GIT_EMAIL = os.environ.get("GIT_USER_EMAIL", "kagent-doer@users.noreply.github.com")
CLONE = "/tmp/repo"
CRON_RE = re.compile(r"^(\S+\s+){4}\S+$")


def log(m):
    print(m, flush=True)


def pushover(title, message):
    if not (PUSH_TOKEN and PUSH_USER):
        log("(no pushover creds; skipping notify)")
        return
    data = urllib.parse.urlencode({
        "token": PUSH_TOKEN, "user": PUSH_USER,
        "title": title[:250], "message": message[:1000],
    }).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(
            "https://api.pushover.net/1/messages.json", data=data), timeout=30)
        log("pushover sent")
    except Exception as e:
        log("pushover failed: %r" % e)


def ask_agent(prompt):
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "message/send",
        "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": prompt}],
                               "messageId": "doer", "kind": "message"}},
    }).encode()
    req = urllib.request.Request(AGENT_URL, data=body, headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"})
    raw = urllib.request.urlopen(req, timeout=400).read().decode()
    d = json.loads(raw)
    parts = []
    for a in (d.get("result", {}).get("artifacts") or []):
        parts += a.get("parts") or []
    msg = (d.get("result", {}).get("status", {}) or {}).get("message") or {}
    parts += msg.get("parts") or []
    texts = [p.get("text", "") for p in parts if p.get("kind") == "text"]
    return (texts[-1] if texts else ""), d.get("result", {}).get("status", {}).get("state", "?")


def extract_proposal(text):
    """Lenient: any fenced block whose body is a JSON object, else a bare {...}."""
    for block in re.findall(r"```[a-zA-Z0-9_-]*\s*(.*?)```", text, re.DOTALL):
        block = block.strip()
        try:
            obj = json.loads(block)
            if isinstance(obj, dict) and obj.get("action"):
                return obj
        except Exception:
            pass
    m = re.search(r"\{[^{}]*\"action\"[^{}]*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


def validate(p):
    if not isinstance(p, dict):
        return "not a JSON object"
    action = p.get("action")
    if action not in ALLOWED_ACTIONS:
        return "action %r not in allowlist %s" % (action, sorted(ALLOWED_ACTIONS))
    if not p.get("target"):
        return "missing target"
    nv = p.get("new_value")
    if not isinstance(nv, str) or not nv.strip():
        return "missing/blank new_value"
    if action == "set_cron" and not CRON_RE.match(nv.strip()):
        return "new_value %r is not a 5-field cron" % nv
    f = p.get("file")
    if f is not None:
        if ".." in f or f.startswith("/") or not f.startswith(PATH_PREFIX) or not f.endswith((".yaml", ".yml")):
            return "file %r outside allowlist %r" % (f, PATH_PREFIX)
    return None


def git(*args, **kw):
    return subprocess.run(["git", "-C", CLONE, *args], check=True,
                          capture_output=True, text=True, **kw)


def gh_api(method, path, payload=None):
    url = "https://api.github.com" + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + TOKEN,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def find_and_apply(action, target, new_value):
    """Scan the clone for the target object and apply the intent deterministically.
    Returns the repo-relative file path changed, or None if not found/no-op."""
    from ruamel.yaml import YAML
    yaml = YAML()
    yaml.preserve_quotes = True
    kind = {"set_cron": "RecurringJob"}[action]
    root = os.path.join(CLONE, PATH_PREFIX)
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if not fn.endswith((".yaml", ".yml")):
                continue
            full = os.path.join(dirpath, fn)
            try:
                with open(full) as fh:
                    docs = list(yaml.load_all(fh))
            except Exception:
                continue
            changed = False
            for doc in docs:
                if not isinstance(doc, dict):
                    continue
                if doc.get("kind") == kind and (doc.get("metadata") or {}).get("name") == target:
                    if action == "set_cron":
                        spec = doc.setdefault("spec", {})
                        if str(spec.get("cron")) != new_value:
                            spec["cron"] = new_value
                            changed = True
            if changed:
                with open(full, "w") as fh:
                    yaml.dump_all(docs, fh)
                return os.path.relpath(full, CLONE)
    return None


def main():
    # PROMPT_OVERRIDE is a testability hook: it changes only what the agent is ASKED,
    # never what the pipeline ACCEPTS — every proposal still passes the same schema +
    # path allowlist + action allowlist below, so it cannot widen the blast radius.
    prompt = os.environ.get("PROMPT_OVERRIDE") or (
        "Using your read-only tools, sweep for storage-durability risks (stale "
        "Longhorn backups, misconfigured RecurringJob schedules, capacity, orphans). "
        "Give a terse human triage first. THEN, only if there is a concrete, safe, "
        "config-level fix, append a fenced ```json block with EXACTLY these keys: "
        "{\"action\":\"set_cron\",\"target\":\"<RecurringJob name>\","
        "\"new_value\":\"<valid 5-field cron>\",\"rationale\":\"<one sentence>\"}. "
        "Only propose set_cron for a RecurringJob whose schedule is clearly wrong. "
        "If there is no safe config fix, emit NO json block. Never propose deletions.")
    text, state = ask_agent(prompt)
    log("agent state=%s" % state)
    log("----- triage -----\n%s\n------------------" % text)

    proposal = extract_proposal(text)
    if not proposal:
        if "ALL CLEAR" not in text and text.strip():
            pushover("storage doer: triage (no PR)", text)
        else:
            log("no proposal, ALL CLEAR / empty — nothing to do")
        return

    err = validate(proposal)
    if err:
        log("proposal REJECTED: %s :: %r" % (err, proposal))
        pushover("storage doer: proposal rejected", "%s\n%s" % (err, json.dumps(proposal)[:400]))
        return

    action = proposal["action"]
    target = proposal["target"]
    new_value = proposal["new_value"].strip()
    rationale = re.sub(r"[^\x20-\x7e]", " ", str(proposal.get("rationale", "")))[:300]
    log("proposal ACCEPTED: %s %s -> %r" % (action, target, new_value))

    if not TOKEN:
        log("INERT (no GITHUB_PR_TOKEN) — would open a PR for the above")
        pushover("storage doer: proposal (inert, no token)",
                 "%s %s -> %s\n%s" % (action, target, new_value, rationale))
        return

    subprocess.run(["git", "clone", "--depth", "1",
                    "https://x-access-token:%s@github.com/%s" % (TOKEN, REPO), CLONE],
                   check=True, capture_output=True, text=True)
    git("config", "user.name", GIT_NAME)
    git("config", "user.email", GIT_EMAIL)

    changed_file = find_and_apply(action, target, new_value)
    if not changed_file:
        log("target not found or already correct — no-op, no PR")
        pushover("storage doer: no-op", "target %s not found / already %s" % (target, new_value))
        return

    key = hashlib.sha256(("%s|%s|%s" % (action, target, new_value)).encode()).hexdigest()[:10]
    branch = "doer/%s-%s-%s" % (AGENT_NAME, action, key)
    owner = REPO.split("/")[0]

    existing = gh_api("GET", "/repos/%s/pulls?state=open&head=%s:%s" % (REPO, owner, branch))
    if existing:
        url = existing[0].get("html_url")
        log("PR already open: %s" % url)
        pushover("storage doer: PR already open", url or branch)
        return

    git("checkout", "-b", branch)
    git("add", changed_file)
    git("commit", "-m",
        "fix(storage): %s %s (kagent doer proposal)\n\n%s" % (action, target, rationale))
    git("push", "origin", branch)

    body = (
        "Proposed by the **storage doer** (local model), applied deterministically by "
        "the doer pipeline (the model did not write this diff).\n\n"
        "- **action:** `%s`\n- **target:** `%s`\n- **new value:** `%s`\n- **file:** `%s`\n\n"
        "**Rationale:** %s\n\n"
        "**Reversible:** `git revert` after merge. Review before merging — the model "
        "only proposed the intent; a human gate (this PR) is the approval.\n\n"
        "_Read-only agent + non-LLM pipeline; the model never held a credential._"
        % (action, target, new_value, changed_file, rationale or "(none given)"))
    pr = gh_api("POST", "/repos/%s/pulls" % REPO, {
        "title": "fix(storage): %s %s (kagent doer)" % (action, target),
        "head": branch, "base": "main", "body": body})
    url = pr.get("html_url")
    log("opened PR: %s" % url)
    pushover("storage doer: PR opened", "%s\n%s %s -> %s" % (url, action, target, new_value))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        log("HTTP ERROR %s: %s" % (e.code, e.read().decode()[:300]))
        pushover("storage doer: FAILED", "HTTP %s" % e.code)
        sys.exit(1)
    except Exception as e:
        log("FATAL: %r" % e)
        pushover("storage doer: FAILED", repr(e)[:300])
        sys.exit(1)
