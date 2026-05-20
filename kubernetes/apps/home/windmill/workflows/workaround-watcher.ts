// Cron: weekly, Mondays at 09:00 America/New_York.
//
// Implements the `upstream-watcher` mode (HOMELAB-SPEC Layer 4 +
// `.agents/skills/upstream-watcher.md` + `.agents/instructions/workarounds.md`).
//
// 1. Fetch open `workaround`-labeled issues on rwlove/home-ops.
// 2. For each tracking issue, parse `Upstream: <url>` from the body.
// 3. Fetch the upstream issue/PR state via the GitHub REST API.
// 4. If upstream is closed/merged → flag for removal.
// 5. ntfy + Zulip a summary if any removals are due.
//
// Anonymous GitHub API access (60 req/hr) is sufficient for the weekly
// cadence + the small number of workarounds expected.

type GhIssue = {
    number: number;
    title: string;
    html_url: string;
    body: string | null;
    state: "open" | "closed";
    state_reason?: string | null;
    pull_request?: { merged_at: string | null };
};

type WorkaroundCheckResult = {
    tracking_number: number;
    tracking_title: string;
    tracking_url: string;
    upstream_url: string | null;
    upstream_state: "open" | "closed" | "merged" | "unknown" | "unparseable";
    removable: boolean;
    note?: string;
};

const HOME_OPS = "rwlove/home-ops";
const GH_BASE = "https://api.github.com";
const TIMEOUT_MS = 30_000;
const UPSTREAM_RE = /^[Uu]pstream:\s*(https?:\/\/[^\s]+)/m;

export async function main() {
    // 1. List tracking issues.
    const trackingResp = await fetch(
        `${GH_BASE}/repos/${HOME_OPS}/issues?labels=workaround&state=open&per_page=100`,
        {
            headers: { Accept: "application/vnd.github+json" },
            signal: AbortSignal.timeout(TIMEOUT_MS),
        },
    );
    if (!trackingResp.ok) {
        return {
            skip: true,
            reason: `tracking issue list: HTTP ${trackingResp.status}`,
        };
    }
    const tracking = (await trackingResp.json().catch(() => [])) as GhIssue[];
    if (!Array.isArray(tracking) || tracking.length === 0) {
        return { tracking_count: 0, removable_count: 0 };
    }

    // 2-4. Check each upstream.
    const results: WorkaroundCheckResult[] = [];
    for (const issue of tracking) {
        results.push(await checkOne(issue));
    }

    const removable = results.filter((r) => r.removable);

    // 5. Notify if any removable.
    if (removable.length > 0) {
        await notifyRemovable(removable);
    }

    return {
        tracking_count: tracking.length,
        removable_count: removable.length,
        results,
    };
}

async function checkOne(tracking: GhIssue): Promise<WorkaroundCheckResult> {
    const base: Omit<WorkaroundCheckResult, "upstream_state" | "removable"> = {
        tracking_number: tracking.number,
        tracking_title: tracking.title,
        tracking_url: tracking.html_url,
        upstream_url: null,
    };

    const m = (tracking.body ?? "").match(UPSTREAM_RE);
    if (!m) {
        return {
            ...base,
            upstream_state: "unparseable",
            removable: false,
            note: "no `Upstream: <url>` line in tracking issue body",
        };
    }
    const upstreamUrl = m[1];

    const parsed = parseGhUrl(upstreamUrl);
    if (!parsed) {
        return {
            ...base,
            upstream_url: upstreamUrl,
            upstream_state: "unparseable",
            removable: false,
            note: "upstream URL not on github.com/<owner>/<repo>/{issues,pull}/<n>",
        };
    }

    const apiUrl = `${GH_BASE}/repos/${parsed.owner}/${parsed.repo}/issues/${parsed.number}`;
    const upstreamResp = await fetch(apiUrl, {
        headers: { Accept: "application/vnd.github+json" },
        signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!upstreamResp.ok) {
        return {
            ...base,
            upstream_url: upstreamUrl,
            upstream_state: "unknown",
            removable: false,
            note: `upstream fetch failed: HTTP ${upstreamResp.status}`,
        };
    }
    const upstream = (await upstreamResp.json()) as GhIssue;

    if (upstream.pull_request) {
        const merged = upstream.pull_request.merged_at != null;
        return {
            ...base,
            upstream_url: upstreamUrl,
            upstream_state: merged ? "merged" : (upstream.state as "open" | "closed"),
            removable: merged,
            note: merged ? "upstream PR merged" : undefined,
        };
    }

    return {
        ...base,
        upstream_url: upstreamUrl,
        upstream_state: upstream.state,
        removable: upstream.state === "closed",
        note: upstream.state === "closed" ? "upstream issue closed" : undefined,
    };
}

function parseGhUrl(
    url: string,
): { owner: string; repo: string; number: number } | null {
    // Matches https://github.com/<owner>/<repo>/issues/<n>
    // OR  https://github.com/<owner>/<repo>/pull/<n>
    const m = url.match(
        /^https?:\/\/github\.com\/([^/]+)\/([^/]+)\/(?:issues|pull)\/(\d+)/,
    );
    if (!m) return null;
    return { owner: m[1], repo: m[2], number: Number(m[3]) };
}

async function notifyRemovable(removable: WorkaroundCheckResult[]) {
    const lines = removable.map(
        (r) =>
            `• #${r.tracking_number} ${r.tracking_title} — ${r.upstream_state} (${r.upstream_url})`,
    );
    const ntfyUrl = Deno.env.get("NTFY_URL") ?? "http://ntfy.home.svc.cluster.local:8080";
    const ntfyToken = Deno.env.get("NTFY_WRITE_TOKEN");
    if (!ntfyToken) return;
    await fetch(`${ntfyUrl}/operations`, {
        method: "POST",
        headers: {
            Authorization: `Bearer ${ntfyToken}`,
            Title: `🔧 ${removable.length} workaround(s) ready to retire`,
            Priority: "3",
            Tags: "wrench",
        },
        body: lines.join("\n"),
        signal: AbortSignal.timeout(TIMEOUT_MS),
    }).catch(() => {});
}
