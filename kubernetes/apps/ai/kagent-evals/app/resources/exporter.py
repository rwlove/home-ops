#!/usr/bin/env python3
"""kagent evals exporter — the doer PR-outcome money metric, as Prometheus gauges.

The single best "is the fleet earning its keep?" signal lives in GitHub: a doer-opened
PR that gets MERGED was useful; one that gets CLOSED was noise. This tiny exporter
counts doer PRs by state (via the GitHub search API — home-ops is public, so no auth)
and serves them at /metrics for Prometheus to scrape. Refreshed on a slow interval to
stay well under the unauthenticated search rate limit.

    kagent_doer_prs{state="open|merged|closed"}   # closed = closed-unmerged
    kagent_evals_scrape_success                    # 1 if the last GitHub refresh worked

Per-agent breakdown (fix(<agent>): titles) is a later addition once a read-only token
raises the rate limit. Stdlib only.
"""
import json
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = "rwlove/home-ops"
REFRESH_SECONDS = 300
SEARCH = "https://api.github.com/search/issues"
_state = {"metrics": "", "success": 0}
_lock = threading.Lock()


def _count(qualifier):
    q = 'repo:%s is:pr in:title "kagent doer" %s' % (REPO, qualifier)
    url = SEARCH + "?" + urllib.parse.urlencode({"q": q, "per_page": 1})
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "kagent-evals-exporter"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return int(json.loads(r.read().decode()).get("total_count", 0))


def refresh():
    try:
        counts = {"open": _count("is:open"),
                  "merged": _count("is:merged"),
                  "closed": _count("is:unmerged is:closed")}
        lines = [
            "# HELP kagent_doer_prs Doer-opened pull requests by state (closed = closed-unmerged).",
            "# TYPE kagent_doer_prs gauge",
        ]
        for st, n in counts.items():
            lines.append('kagent_doer_prs{state="%s"} %d' % (st, n))
        total_acted = counts["merged"] + counts["closed"]
        rate = (counts["merged"] / total_acted) if total_acted else 0.0
        lines += [
            "# HELP kagent_doer_pr_merge_rate Fraction of decided doer PRs that were merged (useful).",
            "# TYPE kagent_doer_pr_merge_rate gauge",
            "kagent_doer_pr_merge_rate %.4f" % rate,
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
