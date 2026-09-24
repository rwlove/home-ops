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
PROM_URL = os.environ.get("PROM_URL", "").rstrip("/")  # Prometheus base for capacity checks
CLONE = "/tmp/repo"
CRON_RE = re.compile(r"^(\S+\s+){4}\S+$")
RETAIN_RE = re.compile(r"^\d+$")
FOR_RE = re.compile(r"^\d+[smhdwy]$")
MEM_RE = re.compile(r"^\d+(\.\d+)?(Ki|Mi|Gi|Ti|Pi|K|M|G|T|P)$")
CPU_RE = re.compile(r"^(\d+m|\d+(\.\d+)?)$")  # 500m (millicores) or 2 / 1.5 (cores)


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
    if nv is None or (isinstance(nv, str) and not nv.strip()):
        return "missing/blank new_value"
    nv = str(nv).strip()
    if action == "set_cron" and not CRON_RE.match(nv):
        return "new_value %r is not a 5-field cron" % nv
    if action == "set_retain" and not RETAIN_RE.match(nv):
        return "new_value %r is not a non-negative integer (retain)" % nv
    if action == "set_for" and not FOR_RE.match(nv):
        return "new_value %r is not a Prometheus duration like 5m/1h (for)" % nv
    if action == "set_mem_limit" and not MEM_RE.match(nv):
        return "new_value %r is not a memory quantity like 2Gi/512Mi" % nv
    if action == "set_cpu_limit" and not CPU_RE.match(nv):
        return "new_value %r is not a cpu quantity like 500m/2/1.5" % nv
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


DOC_SEP = re.compile(r"^---\s*$")
NOOP = "NOOP"  # apply() sentinel: target found but already correct (→ no PR)


def _apply_scalar(lines, target, new_value, field, quote):
    """Replace the single `<field>:` line inside the doc whose metadata.name ==
    target. Returns the line index changed, NOOP if already correct, or None if the
    target's field is not in these lines. Minimal 1-line diff."""
    name_re = re.compile(r"^\s*name:\s*%s\s*$" % re.escape(target))
    field_re = re.compile(r"^(\s*%s:\s*).*$" % re.escape(field))
    ni = next((i for i, ln in enumerate(lines) if name_re.match(ln)), None)
    if ni is None:
        return None
    for j in range(ni + 1, len(lines)):
        if DOC_SEP.match(lines[j]):
            break
        m = field_re.match(lines[j])
        if m:
            # NOOP if the value already matches SEMANTICALLY (ignore quoting) — never
            # open a PR just to add/remove quotes.
            cur = lines[j][len(m.group(1)):].strip().strip("\"'")
            if cur == new_value:
                return NOOP
            val = ('"%s"' % new_value) if quote else new_value
            lines[j] = m.group(1) + val
            return j
    return None


def _apply_for(lines, target, new_value):
    """Set (or insert) the `for:` clause on the alert rule named `target` inside a
    PrometheusRule. Replaces an existing `for:`; otherwise inserts one right after
    the `- alert:` line at the rule-field indent. Minimal diff (1 line changed or
    1 line added)."""
    alert_re = re.compile(r"^(\s*)-\s+alert:\s*%s\s*$" % re.escape(target))
    ai = next((i for i, ln in enumerate(lines) if alert_re.match(ln)), None)
    if ai is None:
        return None
    indent = alert_re.match(lines[ai]).group(1)
    field_indent = indent + "  "
    for_re = re.compile(r"^%sfor:\s*.*$" % re.escape(field_indent))
    for j in range(ai + 1, len(lines)):
        if DOC_SEP.match(lines[j]):
            break
        # dedent back to the list-item level or above → out of this rule block
        if lines[j].strip() and (len(lines[j]) - len(lines[j].lstrip())) <= len(indent):
            break
        if for_re.match(lines[j]):
            # NOOP if the for: already matches semantically (ignore quoting). Write
            # UNQUOTED to match the repo's PrometheusRule convention (`for: 5m`).
            cur = lines[j].split("for:", 1)[1].strip().strip("\"'")
            if cur == new_value:
                return NOOP
            lines[j] = "%sfor: %s" % (field_indent, new_value)
            return j
    lines.insert(ai + 1, "%sfor: %s" % (field_indent, new_value))
    return ai + 1


def _guard_retain_increase_only(docs, target, new_value):
    """Storage prime directive: retain may only INCREASE (never drop backups)."""
    for d in docs:
        if (isinstance(d, dict) and d.get("kind") == "RecurringJob"
                and (d.get("metadata") or {}).get("name") == target):
            cur = (d.get("spec") or {}).get("retain")
            try:
                if int(new_value) <= int(cur):
                    return "set_retain must INCREASE retention (%s -> %s rejected)" % (cur, new_value)
            except (TypeError, ValueError):
                return "retain values must be integers"
            return None
    return "SKIP"  # target not a RecurringJob in this file — keep scanning


_MEM_MULT = {"": 1, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15,
             "Ki": 2 ** 10, "Mi": 2 ** 20, "Gi": 2 ** 30, "Ti": 2 ** 40, "Pi": 2 ** 50}


def _mem_bytes(s):
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(Ki|Mi|Gi|Ti|Pi|K|M|G|T|P|)$", str(s).strip().strip("\"'"))
    return float(m.group(1)) * _MEM_MULT[m.group(2)] if m else None


def _cpu_millicores(s):
    """k8s CPU quantity -> millicores: '500m'->500, '2'->2000, '1.5'->1500."""
    s = str(s).strip().strip("\"'")
    if s.endswith("m"):
        try:
            return float(s[:-1])
        except ValueError:
            return None
    try:
        return float(s) * 1000
    except ValueError:
        return None


# Per-resource capacity math. prom_scale converts a Prometheus allocatable value
# (bytes for memory, cores for cpu) into the unit parse() returns.
RESOURCES = {
    "memory": {"parse": _mem_bytes, "prom_scale": 1, "div": 2 ** 30, "unit": "GiB"},
    "cpu": {"parse": _cpu_millicores, "prom_scale": 1000, "div": 1000, "unit": "cores"},
}


def _apply_limit(lines, target, new_value, field):
    """Set resources.limits.<field> (memory|cpu) on the HelmRelease named `target`,
    INCREASE-only. SAFETY: acts only when EXACTLY ONE `<field>:` sits under a `limits:`
    block in the target's doc (unambiguous single-container app) — otherwise no-op (no
    PR). A decrease is rejected. Minimal 1-line diff."""
    parse = RESOURCES[field]["parse"]
    # Match `name: X` OR `name: &anchor X` — home-ops HelmReleases commonly write
    # `metadata.name: &app <name>`, which a bare-name regex would miss.
    name_re = re.compile(r"^\s*name:\s*(?:&\S+\s+)?%s\s*$" % re.escape(target))
    ni = next((i for i, ln in enumerate(lines) if name_re.match(ln)), None)
    if ni is None:
        return None
    # bound the target's YAML document
    start = ni
    while start > 0 and not DOC_SEP.match(lines[start - 1]):
        start -= 1
    end = ni + 1
    while end < len(lines) and not DOC_SEP.match(lines[end]):
        end += 1
    # collect `<field>:` lines directly under a `limits:` block
    limits_re = re.compile(r"^(\s*)limits:\s*$")
    field_re = re.compile(r"^(\s*)%s:\s*(.*)$" % re.escape(field))
    matches = []
    j = start
    while j < end:
        lm = limits_re.match(lines[j])
        if lm:
            lim_ind = len(lm.group(1))
            k = j + 1
            while k < end and (not lines[k].strip()
                               or (len(lines[k]) - len(lines[k].lstrip())) > lim_ind):
                mm = field_re.match(lines[k])
                if mm and (len(mm.group(1)) == lim_ind + 2):
                    matches.append((k, mm.group(1), mm.group(2).strip()))
                k += 1
        j += 1
    if len(matches) != 1:
        return None  # zero or ambiguous → no-op, no PR
    idx, prefix, cur = matches[0]
    curv, newv = parse(cur), parse(new_value)
    if curv is None or newv is None:
        raise ValueError("unparseable %s quantity: cur=%r new=%r" % (field, cur, new_value))
    if newv == curv:
        return NOOP
    if newv < curv:
        raise ValueError("set_%s_limit must INCREASE the limit (%s -> %s rejected)"
                         % (field, cur, new_value))
    lines[idx] = prefix + field + ": " + new_value
    return idx


# action -> {kind, locate substring, apply(lines,target,new_value), optional guard}
ACTIONS = {
    "set_cron": {"kind": "RecurringJob", "locate": lambda t: "name: " + t,
                 "apply": lambda ls, t, v: _apply_scalar(ls, t, v, "cron", quote=True)},
    "set_retain": {"kind": "RecurringJob", "locate": lambda t: "name: " + t,
                   "apply": lambda ls, t, v: _apply_scalar(ls, t, v, "retain", quote=False),
                   "guard": _guard_retain_increase_only},
    "set_for": {"kind": "PrometheusRule", "locate": lambda t: "alert: " + t,
                "apply": _apply_for},
    "set_mem_limit": {"kind": "HelmRelease", "locate": lambda t: t,
                      "apply": lambda ls, t, v: _apply_limit(ls, t, v, "memory")},
    "set_cpu_limit": {"kind": "HelmRelease", "locate": lambda t: t,
                      "apply": lambda ls, t, v: _apply_limit(ls, t, v, "cpu")},
}


def find_and_apply(action, target, new_value):
    """Locate the target and apply the action's deterministic minimal edit. Returns
    the repo-relative path changed, or None if not found / already correct (no-op)."""
    from ruamel.yaml import YAML
    yaml = YAML(typ="safe")
    spec = ACTIONS[action]
    kind, locate, apply = spec["kind"], spec["locate"](target), spec["apply"]
    root = os.path.join(CLONE, PATH_PREFIX)
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if not fn.endswith((".yaml", ".yml")):
                continue
            full = os.path.join(dirpath, fn)
            try:
                text = open(full, encoding="utf-8").read()
            except Exception:
                continue
            if kind not in text or locate not in text:
                continue
            guard = spec.get("guard")
            if guard:
                try:
                    docs = list(yaml.load_all(text))
                except Exception:
                    continue
                verdict = guard(docs, target, new_value)
                if verdict == "SKIP":
                    continue
                if verdict:
                    raise ValueError(verdict)
            lines = text.split("\n")
            idx = apply(lines, target, new_value)
            if idx == NOOP:
                return None      # already correct — no PR
            if idx is None:
                continue         # not in this file — keep scanning
            open(full, "w", encoding="utf-8").write("\n".join(lines))
            return os.path.relpath(full, CLONE)
    return None


def _prom_query(expr):
    """Instant single-value Prometheus query; returns a float or None on any failure."""
    if not PROM_URL:
        return None
    try:
        url = PROM_URL + "/api/v1/query?query=" + urllib.parse.quote(expr)
        d = json.loads(urllib.request.urlopen(url, timeout=15).read().decode())
        r = d.get("data", {}).get("result", [])
        return float(r[0]["value"][1]) if r else None
    except Exception as e:
        log("prometheus query failed (%s...): %r" % (expr[:40], e))
        return None


def _capacity_check(new_value, resource):
    """Reject a cpu/memory limit the cluster can't safely hold. Capacity is queried LIVE
    from Prometheus every run — the node's allocatable AND the cluster's overall free
    headroom, nothing hardcoded but the safety fraction. FAIL-CLOSED: if capacity can't
    be verified, refuse rather than raise a limit blind. Returns an error to reject, or
    None to allow."""
    r = RESOURCES[resource]
    newv = r["parse"](new_value)
    if newv is None:
        return "unparseable %s quantity %r" % (resource, new_value)
    max_node = _prom_query('max(kube_node_status_allocatable{resource="%s"})' % resource)
    if max_node is None:
        return ("CANNOT VERIFY %s capacity (Prometheus unreachable) — refusing to raise a limit blind"
                % resource)
    max_node *= r["prom_scale"]
    if newv > 0.9 * max_node:
        return ("%s limit %s exceeds 90%% of the largest node (%.1f %s) — no node could hold it"
                % (resource, new_value, max_node / r["div"], r["unit"]))
    free = _prom_query('sum(kube_node_status_allocatable{resource="%s"}) '
                       '- sum(kube_pod_container_resource_requests{resource="%s"})' % (resource, resource))
    if free is not None:
        free *= r["prom_scale"]
        if newv > free:
            return ("%s limit %s exceeds current cluster free headroom (%.1f %s)"
                    % (resource, new_value, free / r["div"], r["unit"]))
    return None


STORAGE_PROMPT = (
    "Using your read-only tools, sweep for storage-durability risks (stale Longhorn "
    "backups, misconfigured RecurringJob schedules, too-low backup retention, capacity, "
    "orphans). Give a terse human triage first. THEN, only if there is a concrete, safe, "
    "config-level fix, append a fenced ```json block with EXACTLY these keys: "
    "{\"action\":\"<set_cron|set_retain>\",\"target\":\"<RecurringJob name>\","
    "\"new_value\":\"<a 5-field cron for set_cron, or an integer for set_retain>\","
    "\"rationale\":\"<one sentence>\"}. set_cron only for a clearly-wrong schedule; "
    "set_retain only to INCREASE retention (never reduce it). If there is no safe config "
    "fix, emit NO json block. Never propose deletions or anything that reduces protection.")


def main():
    # PROMPT_OVERRIDE / DOER_PROMPT change only what the agent is ASKED, never what the
    # pipeline ACCEPTS — every proposal still passes the same schema + path allowlist +
    # action allowlist below, so they cannot widen the blast radius.
    prompt = os.environ.get("PROMPT_OVERRIDE") or os.environ.get("DOER_PROMPT") or STORAGE_PROMPT
    prompt += (
        "\n\nSTRICT OUTPUT RULES — you are an unattended batch job; there is NO human "
        "to reply to. Never ask a question or request confirmation. Never print a tool "
        "call as text. If you found a safe fix, output ONLY the one fenced ```json "
        "proposal block and nothing else. If you did not, output nothing at all.")
    text, state = ask_agent(prompt)
    log("agent state=%s" % state)
    log("----- triage -----\n%s\n------------------" % text)

    # A doer pages ONLY when it opens a PR (you need to review it) or hard-fails.
    # Everything else — no proposal, invalid proposal, rejected, no-op — is logged and
    # SILENT. A doer that found nothing is supposed to be quiet, not page.
    proposal = extract_proposal(text)
    if not proposal:
        log("no valid proposal — nothing actionable; silent (no page)")
        return

    err = validate(proposal)
    if err:
        log("proposal REJECTED (validation), silent: %s :: %r" % (err, proposal))
        return

    action = proposal["action"]
    target = proposal["target"]
    new_value = str(proposal["new_value"]).strip()
    rationale = re.sub(r"[^\x20-\x7e]", " ", str(proposal.get("rationale", "")))[:300]
    log("proposal ACCEPTED: %s %s -> %r" % (action, target, new_value))

    # Capacity gate (deterministic, live-queried) for anything that raises a resource
    # ceiling — never propose a cpu/memory limit the cluster can't actually hold.
    _RES = {"set_mem_limit": "memory", "set_cpu_limit": "cpu"}
    if action in _RES:
        cap = _capacity_check(new_value, _RES[action])
        if cap:
            log("REJECTED (capacity), silent: %s" % cap)
            return

    if not TOKEN:
        log("INERT (no token) — would open PR: %s %s -> %s" % (action, target, new_value))
        return

    subprocess.run(["git", "clone", "--depth", "1",
                    "https://x-access-token:%s@github.com/%s" % (TOKEN, REPO), CLONE],
                   check=True, capture_output=True, text=True)
    git("config", "user.name", GIT_NAME)
    git("config", "user.email", GIT_EMAIL)

    changed_file = find_and_apply(action, target, new_value)
    if not changed_file:
        log("target not found or already correct — no-op, silent")
        return

    key = hashlib.sha256(("%s|%s|%s" % (action, target, new_value)).encode()).hexdigest()[:10]
    branch = "doer/%s-%s-%s" % (AGENT_NAME, action, key)
    owner = REPO.split("/")[0]

    existing = gh_api("GET", "/repos/%s/pulls?state=open&head=%s:%s" % (REPO, owner, branch))
    if existing:
        log("PR already open (silent, no new PR): %s" % existing[0].get("html_url"))
        return

    git("checkout", "-b", branch)
    git("add", changed_file)
    git("commit", "-m",
        "fix(%s): %s %s (kagent doer proposal)\n\n%s" % (AGENT_NAME, action, target, rationale))
    git("push", "origin", branch)

    body = (
        "Proposed by the **%s doer** (local model), applied deterministically by "
        "the doer pipeline (the model did not write this diff).\n\n"
        "- **action:** `%s`\n- **target:** `%s`\n- **new value:** `%s`\n- **file:** `%s`\n\n"
        "**Rationale:** %s\n\n"
        "**Reversible:** `git revert` after merge. Review before merging — the model "
        "only proposed the intent; a human gate (this PR) is the approval.\n\n"
        "_Read-only agent + non-LLM pipeline; the model never held a credential._"
        % (AGENT_NAME, action, target, new_value, changed_file, rationale or "(none given)"))
    pr = gh_api("POST", "/repos/%s/pulls" % REPO, {
        "title": "fix(%s): %s %s (kagent doer)" % (AGENT_NAME, action, target),
        "head": branch, "base": "main", "body": body})
    url = pr.get("html_url")
    log("opened PR: %s" % url)
    pushover("%s doer: PR opened" % AGENT_NAME,
             "%s\n%s %s -> %s" % (url, action, target, new_value))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        log("HTTP ERROR %s: %s" % (e.code, e.read().decode()[:300]))
        pushover("%s doer: FAILED" % AGENT_NAME, "HTTP %s" % e.code)
        sys.exit(1)
    except Exception as e:
        log("FATAL: %r" % e)
        pushover("%s doer: FAILED" % AGENT_NAME, repr(e)[:300])
        sys.exit(1)
