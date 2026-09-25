#!/usr/bin/env python3
"""kagent evals exporter — the doer PR-outcome money metric, as Prometheus gauges.

The single best "is the fleet earning its keep?" signal lives in GitHub: a doer-opened
PR that gets MERGED was useful; one that gets CLOSED was noise. This tiny exporter
counts doer PRs by state (via the GitHub search API — home-ops is public, so no auth)
and serves them at /metrics for Prometheus to scrape. Refreshed on a slow interval to
stay well under the unauthenticated search rate limit.

    kagent_doer_prs{state="open|merged|closed"}            # closed = closed-unmerged
    kagent_doer_prs_by_agent{state,agent="<agent>"}        # same, split by doer agent
    kagent_doer_pr_merge_rate                              # global merged / decided
    kagent_doer_pr_merge_rate_by_agent{agent="<agent>"}    # per-agent merged / decided
    kagent_evals_scrape_success                            # 1 if the last GitHub refresh worked

The per-agent breakdown needs NO extra GitHub calls or auth token: the same three
unauthenticated searches already return the matching PR items with their titles, and
the doer title shape `fix(<agent>): ... (kagent doer)` names the agent — so we just
group the items already in hand. (The original "defer until a token raises the rate
limit" note was wrong: grouping is free post-processing, not more requests.) Stdlib
only. Caveat unchanged from the count-only version: each search returns only the first
100 items, so counts (and thus rates) undercount once a single state exceeds 100 doer
PRs — well beyond current volume; add pagination if that day comes.
"""
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = "rwlove/home-ops"
REFRESH_SECONDS = 300
SEARCH = "https://api.github.com/search/issues"
# Only REAL doer PRs: the pipeline titles them `fix(<agent>): <action> <target>
# (kagent doer)`. A plain "kagent doer" text search also matches unrelated infra PRs
# that merely mention the words, so filter returned items by this exact shape. The
# capture group is the doer agent name, used for the per-agent breakdown.
DOER_TITLE = re.compile(r"^fix\(([a-z][a-z0-9-]*)\): .+ \(kagent doer\)$")
_state = {"metrics": "", "success": 0}
_lock = threading.Lock()


def _agents(qualifier):
    # Fetch the matching items (up to 100 — far more than the doer PR volume for a long
    # while) and return the agent name of each item whose title matches the exact doer
    # shape. Counting is len(); the per-agent split is Counter() over the same list.
    q = 'repo:%s is:pr in:title "kagent doer" %s' % (REPO, qualifier)
    url = SEARCH + "?" + urllib.parse.urlencode({"q": q, "per_page": 100})
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "kagent-evals-exporter"})
    with urllib.request.urlopen(req, timeout=20) as r:
        items = json.loads(r.read().decode()).get("items", [])
    out = []
    for it in items:
        m = DOER_TITLE.match(it.get("title", ""))
        if m:
            out.append(m.group(1))
    return out


def refresh():
    try:
        by_state = {"open": _agents("is:open"),
                    "merged": _agents("is:merged"),
                    "closed": _agents("is:unmerged is:closed")}
        counts = {st: len(v) for st, v in by_state.items()}
        per_agent = {st: Counter(v) for st, v in by_state.items()}
        agents = sorted(set().union(*(c.keys() for c in per_agent.values())))

        lines = [
            "# HELP kagent_doer_prs Doer-opened pull requests by state (closed = closed-unmerged).",
            "# TYPE kagent_doer_prs gauge",
        ]
        for st, n in counts.items():
            lines.append('kagent_doer_prs{state="%s"} %d' % (st, n))

        lines += [
            "# HELP kagent_doer_prs_by_agent Doer-opened pull requests by state and doer agent.",
            "# TYPE kagent_doer_prs_by_agent gauge",
        ]
        for st in ("open", "merged", "closed"):
            for ag in agents:
                lines.append('kagent_doer_prs_by_agent{state="%s",agent="%s"} %d'
                             % (st, ag, per_agent[st].get(ag, 0)))

        total_acted = counts["merged"] + counts["closed"]
        rate = (counts["merged"] / total_acted) if total_acted else 0.0
        lines += [
            "# HELP kagent_doer_pr_merge_rate Fraction of decided doer PRs that were merged (useful).",
            "# TYPE kagent_doer_pr_merge_rate gauge",
            "kagent_doer_pr_merge_rate %.4f" % rate,
            "# HELP kagent_doer_pr_merge_rate_by_agent Per-agent fraction of decided doer PRs merged.",
            "# TYPE kagent_doer_pr_merge_rate_by_agent gauge",
        ]
        for ag in agents:
            decided = per_agent["merged"].get(ag, 0) + per_agent["closed"].get(ag, 0)
            ar = (per_agent["merged"].get(ag, 0) / decided) if decided else 0.0
            lines.append('kagent_doer_pr_merge_rate_by_agent{agent="%s"} %.4f' % (ag, ar))

        lines += [
            "# HELP kagent_evals_scrape_success 1 if the last GitHub refresh succeeded.",
            "# TYPE kagent_evals_scrape_success gauge",
            "kagent_evals_scrape_success 1",
        ]
        with _lock:
            _state["metrics"] = "\n".join(lines) + "\n"
            _state["success"] = 1
    except Exception as e:  # noqa: BLE001 — surface failure as a metric, never crash
        with _lock:
            _state["success"] = 0
            _state["metrics"] = (
                "# refresh failed: %r\n"
                "# HELP kagent_evals_scrape_success 1 if the last GitHub refresh succeeded.\n"
                "# TYPE kagent_evals_scrape_success gauge\n"
                "kagent_evals_scrape_success 0\n" % str(e)[:120])


def _loop():
    while True:
        refresh()
        time.sleep(REFRESH_SECONDS)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            return
        with _lock:
            body = _state["metrics"].encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # quiet


if __name__ == "__main__":
    refresh()
    threading.Thread(target=_loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
