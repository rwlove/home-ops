#!/usr/bin/env python3
"""Fleet-todo completion follow-up (local-cron, kagent tier).

When a Vikunja "Fleet Todos" task is marked done, by a persona, the laptop
helper or Robert on his phone, the persona that OWNS it (its owner label)
gets a chance to say whether the completion spawns follow-up work. This
runner:

  1. Polls the Fleet Todos project for tasks that are done, were completed
     inside the lookback window (and not before FOLLOWUP_SINCE, so enabling
     this never back-fills historical completions), and do NOT yet carry the
     `followup-reviewed` label. At most MAX_TASKS per run.
  2. For an owner that runs as an in-cluster kagent Agent (KAGENT_PERSONAS),
     invokes that agent via the shared a2a_client with the task and the
     owner's current open todos, and asks for a strict JSON verdict between
     <<<FOLLOWUP>>>...<<<END>>> markers (zero to MAX_FOLLOWUPS new todos).
  3. The RUNNER, not the model, writes: it validates the proposals,
     de-duplicates them against the owner's open todos (case-insensitive
     title match), creates them with the owner label (+ `robert-action` when
     asked), appends a one-line review note to the completed task's
     description, and finally labels it `followup-reviewed`. The agents have
     no Vikunja write tools, so the local model can never mis-target a write
     (same reasoning as crusher_review.py's runner-owned memory write).
  4. Owners with no in-cluster agent (laptop-only personas) get the
     `followup-laptop` + `followup-reviewed` labels instead. The laptop
     `whats-next` skill surfaces `followup-laptop` completions and routes each
     to its persona in the main session.
  5. Emails Robert via notify-mcp ONLY when follow-up todos were actually
     created. A "no follow-up" verdict is recorded on the task, never emailed.

Idempotency (HOMELAB-SPEC L2 #8): the `followup-reviewed` label is the
idempotency key and is applied LAST. A crash after creating follow-ups but
before labelling re-runs the task next time, and the open-title de-dupe stops
the second run from creating the same todos again. An agent or backend
failure leaves the task unlabelled so the next run retries it; the lookback
window bounds retries.

Vikunja's POST /tasks/{id} zeroes any omitted field (due_date, priority, ...),
so updates GET the task first and POST the merged object (same fix as the
laptop _shared/vikunja.py helper).

Data classification: task titles are Internal-tier (the fleet contract keeps
secrets, media names and Restricted specifics out of them). All reasoning runs
on the local kagent model; the only thing leaving the cluster is the short
list of new todo titles in the notify-mcp email to Robert. Stdlib only.

Env: AGENT_URL VIKUNJA_URL VIKUNJA_TOKEN KAGENT_PERSONAS FOLLOWUP_SINCE
     [LOOKBACK_DAYS=7] [MAX_TASKS=5] [MAX_FOLLOWUPS=3] [BUDGET_SECONDS=600]
     [NOTIFY_URL=http://notify-mcp.mcp-system:8060/send] [DRY_RUN=false]
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request

import a2a_client
from english_gate import is_non_english

AGENT_URL = os.environ["AGENT_URL"]
VIKUNJA_URL = os.environ["VIKUNJA_URL"].rstrip("/")
VIKUNJA_TOKEN = os.environ["VIKUNJA_TOKEN"]
KAGENT_PERSONAS = {p.strip() for p in os.environ["KAGENT_PERSONAS"].split(",") if p.strip()}
FOLLOWUP_SINCE = dt.datetime.fromisoformat(
    os.environ["FOLLOWUP_SINCE"].replace("Z", "+00:00"))
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "7"))
MAX_TASKS = int(os.environ.get("MAX_TASKS", "5"))
MAX_FOLLOWUPS = int(os.environ.get("MAX_FOLLOWUPS", "3"))
BUDGET = int(os.environ.get("BUDGET_SECONDS", "600"))
NOTIFY_URL = os.environ.get("NOTIFY_URL", "http://notify-mcp.mcp-system:8060/send")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

PROJECT_TITLE = "Fleet Todos"
REVIEWED = "followup-reviewed"
LAPTOP = "followup-laptop"
ROBERT = "robert-action"
# Labels that are never an owner. Everything else on a task is a persona label.
META_LABELS = {REVIEWED, LAPTOP, ROBERT}

_START = "<<<FOLLOWUP>>>"
_END = "<<<END>>>"


def log(m):
    print(m, flush=True)


# ---------------------------------------------------------------- Vikunja

def _req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(VIKUNJA_URL + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + VIKUNJA_TOKEN)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else None


def _paged(path):
    out, page = [], 1
    sep = "&" if "?" in path else "?"
    while True:
        chunk = _req("GET", "%s%spage=%d&per_page=50" % (path, sep, page))
        if not chunk:
            break
        out.extend(chunk)
        if len(chunk) < 50:
            break
        page += 1
    return out


def project_id():
    for p in _paged("/projects"):
        if p.get("title") == PROJECT_TITLE:
            return p["id"]
    raise RuntimeError("project %r not found" % PROJECT_TITLE)


_LABELS = {}


def label_id(name):
    if not _LABELS:
        for lb in _paged("/labels"):
            _LABELS[lb["title"]] = lb["id"]
    if name not in _LABELS:
        _LABELS[name] = _req("PUT", "/labels", {"title": name})["id"]
    return _LABELS[name]


def add_label(task_id, name):
    try:
        _req("PUT", "/tasks/%d/labels" % task_id, {"label_id": label_id(name)})
    except urllib.error.HTTPError as e:
        # 400 "label already exists on task" is fine (idempotent re-run).
        if e.code != 400:
            raise


def append_note(task_id, note):
    task = _req("GET", "/tasks/%d" % task_id)
    desc = task.get("description") or ""
    task["description"] = (desc + "\n\n" if desc else "") + note
    _req("POST", "/tasks/%d" % task_id, task)


def _labels(task):
    return {lb["title"] for lb in (task.get("labels") or [])}


def owner_of(task):
    owners = sorted(_labels(task) - META_LABELS)
    return owners[0] if owners else None


def _ts(value):
    if not value or value.startswith("0001"):
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _due_rfc3339(day):
    """'YYYY-MM-DD' -> 09:00 America/New_York as UTC RFC3339 (helper parity)."""
    from zoneinfo import ZoneInfo
    d = dt.datetime.combine(dt.date.fromisoformat(day), dt.time(9, 0),
                            tzinfo=ZoneInfo("America/New_York"))
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- agent

def build_prompt(task, owner, open_titles):
    due = _ts(task.get("due_date"))
    lines = [
        "A Fleet Todo you own was just marked DONE. Decide whether completing it "
        "creates follow-up work that is not already tracked.",
        "",
        "Completed todo #%d: %s" % (task["id"], task.get("title", "")),
        "Labels: %s" % ", ".join(sorted(_labels(task))),
        "Due: %s" % (due.date().isoformat() if due else "none"),
        "Completed at: %s" % task.get("done_at", ""),
        "Description:",
        (task.get("description") or "(none)").strip()[:3000],
        "",
        "Your OTHER open todos (do NOT propose anything already covered here):",
    ]
    lines += ["- " + t for t in open_titles] or ["- (none)"]
    lines += [
        "",
        "Use your read-only tools if you need current state to decide. Propose a "
        "follow-up ONLY when there is a concrete next action implied by this "
        "completion (a recheck, a next step, a deadline that now starts). "
        "Zero follow-ups is the normal, correct answer for most completions.",
        "",
        "OUTPUT CONTRACT, follow exactly:",
        "- Reply in ENGLISH ONLY. One-way: never ask a question.",
        "- Never fabricate a date, number or fact; leave due null if unknown.",
        "- Titles are web-visible: no secrets, no media file names, no Restricted "
        "specifics (no diagnoses, doses, account numbers). Keep detail generic.",
        "- At most %d follow-ups." % MAX_FOLLOWUPS,
        "- Output EXACTLY ONE block and nothing else:",
        _START,
        '{"followups": [{"title": "<=100 chars", "due": "YYYY-MM-DD or null", '
        '"priority": 0-5, "robert_action": true|false, "description": "short why"}], '
        '"note": "one sentence: why these, or why none"}',
        _END,
    ]
    return "\n".join(lines)


def parse_verdict(text):
    """Return (followups, note), or (None, reason) when the reply is unusable.

    Tolerant of the local model's habits: narration before the block, a code
    fence around the JSON, trailing text after it, or a missing <<<END>>>.
    The JSON object is read with raw_decode from the first "{" after the
    start marker (or from a bare '{"followups"' when the marker is missing).
    `reason` is a short code safe to log: it never contains reply text,
    because a concierge persona's reply can carry Restricted detail.
    """
    if not text:
        return None, "empty"
    if _START in text:
        at = text.find("{", text.rfind(_START) + len(_START))
    else:
        at = text.find('{"followups"')
    if at == -1:
        return None, "no-json-start"
    try:
        obj, _ = json.JSONDecoder().raw_decode(text, at)
    except ValueError as e:
        return None, "json-error:%s" % e.msg
    if not isinstance(obj, dict):
        return None, "json-not-object"
    fus = obj.get("followups")
    if not isinstance(fus, list):
        return None, "no-followups-list"
    clean = []
    for f in fus[:MAX_FOLLOWUPS]:
        if not isinstance(f, dict):
            continue
        title = re.sub(r"\s+", " ", str(f.get("title") or "")).strip()[:100]
        if not title:
            continue
        due = f.get("due")
        if not (isinstance(due, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", due)):
            due = None
        try:
            prio = max(0, min(5, int(f.get("priority") or 0)))
        except (TypeError, ValueError):
            prio = 0
        clean.append({
            "title": title,
            "due": due,
            "priority": prio,
            "robert_action": f.get("robert_action") is True,
            "description": str(f.get("description") or "").strip()[:1000],
        })
    return clean, str(obj.get("note") or "").strip()[:300]


def _norm(title):
    return re.sub(r"^\[[^\]]*\]\s*", "", title).strip().lower()


# ---------------------------------------------------------------- main

def review_kagent(pid, task, owner, open_titles):
    """Returns list of created '#id title' strings, or None to retry later."""
    prompt = build_prompt(task, owner, open_titles)
    text, state = a2a_client.call_agent(AGENT_URL, owner, prompt, budget=BUDGET,
                                        message_id="todo-followup-%d" % task["id"])
    if not text or is_non_english(text):
        log("#%d: no usable reply from %s (state=%s), retry next run"
            % (task["id"], owner, state))
        return None
    followups, note = parse_verdict(text)
    if followups is None:
        log("#%d: %s reply broke the contract (%s, state=%s, len=%d), retry next run"
            % (task["id"], owner, note, state, len(text)))
        return None

    existing = {_norm(t) for t in open_titles}
    created = []
    for f in followups:
        if _norm(f["title"]) in existing:
            log("#%d: skip duplicate follow-up %r" % (task["id"], f["title"]))
            continue
        title = "[%s] %s" % (owner, f["title"])
        body = {"title": title,
                "description": ((f["description"] + "\n\n") if f["description"] else "")
                + "Follow-up to #%d (auto, todo-followup)." % task["id"],
                "priority": f["priority"]}
        if f["due"]:
            body["due_date"] = _due_rfc3339(f["due"])
        if DRY_RUN:
            log("DRY_RUN would create: %s" % json.dumps(body))
            created.append("(dry-run) " + title)
            continue
        new = _req("PUT", "/projects/%d/tasks" % pid, body)
        add_label(new["id"], owner)
        if f["robert_action"]:
            add_label(new["id"], ROBERT)
        existing.add(_norm(f["title"]))
        created.append("#%d %s" % (new["id"], title))

    stamp = dt.date.today().isoformat()
    summary = ("created " + "; ".join(created)) if created else "no follow-up"
    note_line = "Follow-up review %s (%s): %s.%s" % (
        stamp, owner, summary, (" " + note) if note else "")
    if not DRY_RUN:
        append_note(task["id"], note_line)
        add_label(task["id"], REVIEWED)
    log("#%d: %s" % (task["id"], note_line))
    return created


def main():
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = max(FOLLOWUP_SINCE, now - dt.timedelta(days=LOOKBACK_DAYS))
    try:
        pid = project_id()
        tasks = _paged("/projects/%d/tasks" % pid)
    except Exception as e:
        # Vikunja unreachable: silent skipped run, the next poll retries.
        log("Vikunja unreachable: %r" % e)
        log("TODO_FOLLOWUP_RESULT=skipped reason=vikunja-unreachable")
        return 0

    candidates = []
    for t in tasks:
        done_at = _ts(t.get("done_at"))
        if not t.get("done") or not done_at or done_at < cutoff:
            continue
        if REVIEWED in _labels(t):
            continue
        candidates.append((done_at, t))
    candidates.sort(key=lambda c: c[0])
    candidates = [t for _, t in candidates[:MAX_TASKS]]
    if not candidates:
        log("TODO_FOLLOWUP_RESULT=idle")
        return 0

    open_by_owner = {}
    for t in tasks:
        if not t.get("done"):
            open_by_owner.setdefault(owner_of(t), []).append(t.get("title", ""))

    created_all, reviewed, retried, laptop = [], 0, 0, 0
    for task in candidates:
        owner = owner_of(task)
        try:
            if owner in KAGENT_PERSONAS:
                created = review_kagent(pid, task, owner, open_by_owner.get(owner, []))
                if created is None:
                    retried += 1
                    continue
                created_all += created
                reviewed += 1
            else:
                # Laptop-only persona (or unowned): hand off to whats-next.
                if not DRY_RUN:
                    append_note(task["id"], "Follow-up review %s: %s has no in-cluster "
                                "agent; queued for the laptop session (whats-next)."
                                % (dt.date.today().isoformat(), owner or "no owner"))
                    add_label(task["id"], LAPTOP)
                    add_label(task["id"], REVIEWED)
                log("#%d: owner %s is laptop-only, labelled %s" % (task["id"], owner, LAPTOP))
                laptop += 1
        except Exception as e:
            log("#%d: error %r, retry next run" % (task["id"], e))
            retried += 1

    if created_all and not DRY_RUN:
        body = ("**%d new follow-up todo%s** from completed Fleet Todos:\n\n"
                % (len(created_all), "" if len(created_all) == 1 else "s")
                + "\n".join("- " + c for c in created_all))
        try:
            data = json.dumps({"subject": "todo follow-ups — %s" % dt.date.today().isoformat(),
                               "body": body, "agent": "radar"}).encode()
            urllib.request.urlopen(urllib.request.Request(
                NOTIFY_URL, data=data, headers={"Content-Type": "application/json"}),
                timeout=30).read()
            log("follow-up email sent")
        except Exception as e:
            log("WARN: notify-mcp delivery failed: %r" % e)

    log("TODO_FOLLOWUP_RESULT=ran reviewed=%d laptop=%d retry=%d created=%d dry_run=%s"
        % (reviewed, laptop, retried, len(created_all), DRY_RUN))
    return 0


if __name__ == "__main__":
    sys.exit(main())
