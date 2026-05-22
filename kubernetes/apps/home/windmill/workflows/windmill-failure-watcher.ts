// Periodic self-check: detect Windmill scripts that are failing at a
// high rate and notify Rob via ntfy + Zulip DM.
//
// Why: PR #11894 fixed alertmanager-holmesgpt-notify in git on
// 2026-05-21, but the change was never pushed to Windmill via
// `wmill sync`. Every alertmanager → Holmes call failed silently for
// 24+ hours. There was no detection because the alert path itself
// was broken — the bridge that would have notified Rob was the thing
// failing. This watcher closes that gap.
//
// Trigger: Windmill cron, recommended */5 * * * *. Schedule lives in
// Windmill state (set via /api/w/lovenet/schedules/create); not part
// of the git checkout. See PR description for the one-time bootstrap.
//
// Notification budget: at most one DM per script_path per hour, even
// if failures keep accumulating, to avoid notification floods. The
// per-hour dedup is in-memory (script invocation state — Windmill
// persists `state` across invocations of the same script when
// invoked via setState/getState helpers from npm:windmill-client).
//
// Self-recursion risk: if this watcher itself fails, no one knows.
// Mitigation: BlackboxProbe on windmill /api/version catches the
// "Windmill is down" case; the watcher's own failures show up in
// `wmill flows runs` and the langgraph-dlq-watcher Pushover.

import * as wmill from "npm:windmill-client@1.527.0";

type CompletedJob = {
    id: string;
    script_path?: string;
    runnable_path?: string;
    success: boolean;
    started_at: string;
    duration_ms: number;
};

const WORKSPACE = "lovenet";
const WMILL_BASE = "http://windmill-app.home.svc.cluster.local:8000";
const LOOKBACK_MIN = 60;
const MIN_INVOCATIONS = 3;
const FAILURE_RATE_THRESHOLD = 0.5;
const NOTIFY_COOLDOWN_S = 3600;

// Scripts we expect to fail occasionally (DLQ scans, smoke probes) —
// surface them only if failure rate stays >90% over the window, to
// avoid flapping when one bad poll trips the regular threshold.
const TOLERANT_PATHS = new Set([
    "f/lovenet/langgraph-dlq-watcher",
]);
const TOLERANT_THRESHOLD = 0.9;

type WatcherState = {
    last_notified_at?: Record<string, number>;
};

export async function main() {
    const token = Deno.env.get("WINDMILL_TOKEN");
    if (!token) throw new Error("WINDMILL_TOKEN env not set");

    const sinceMs = Date.now() - LOOKBACK_MIN * 60 * 1000;
    const startedAfter = new Date(sinceMs).toISOString();

    const jobs = await fetchJobs(token, startedAfter);
    const grouped = groupByPath(jobs);
    const issues = grouped.filter(matchesThreshold);

    const state = (await wmill.getState()) as WatcherState | null;
    const lastNotified = state?.last_notified_at ?? {};
    const nowS = Math.floor(Date.now() / 1000);

    const dueForNotify = issues.filter((g) => {
        const last = lastNotified[g.path] ?? 0;
        return nowS - last >= NOTIFY_COOLDOWN_S;
    });

    const updatedLastNotified = { ...lastNotified };
    for (const g of dueForNotify) {
        updatedLastNotified[g.path] = nowS;
    }
    await wmill.setState({
        last_notified_at: updatedLastNotified,
    } satisfies WatcherState);

    if (dueForNotify.length === 0) {
        return {
            checked_n: jobs.length,
            grouped_n: grouped.length,
            issues_n: issues.length,
            new_notifications: 0,
        };
    }

    await notify(dueForNotify);

    return {
        checked_n: jobs.length,
        grouped_n: grouped.length,
        issues_n: issues.length,
        new_notifications: dueForNotify.length,
        notified_paths: dueForNotify.map((g) => g.path),
    };
}

// ---------- Job fetch ----------

async function fetchJobs(token: string, startedAfter: string): Promise<CompletedJob[]> {
    const url = `${WMILL_BASE}/api/w/${WORKSPACE}/jobs/list_completed`
        + `?per_page=500&started_after=${encodeURIComponent(startedAfter)}`
        + `&job_kinds=script`;
    const r = await fetch(url, {
        headers: { Authorization: `Bearer ${token}` },
        signal: AbortSignal.timeout(15_000),
    });
    if (!r.ok) {
        throw new Error(`jobs/list_completed returned ${r.status}`);
    }
    const data = await r.json();
    return Array.isArray(data) ? data : [];
}

// ---------- Grouping + threshold ----------

type Grouped = {
    path: string;
    total: number;
    failed: number;
    failure_rate: number;
    sample_error?: string;
};

function groupByPath(jobs: CompletedJob[]): Grouped[] {
    const byPath = new Map<string, { total: number; failed: number }>();
    for (const j of jobs) {
        const path = j.script_path ?? j.runnable_path ?? "(unknown)";
        // Skip the watcher itself — would create a feedback loop on
        // any false-positive.
        if (path === "f/lovenet/windmill-failure-watcher") continue;
        const bucket = byPath.get(path) ?? { total: 0, failed: 0 };
        bucket.total += 1;
        if (!j.success) bucket.failed += 1;
        byPath.set(path, bucket);
    }
    return Array.from(byPath.entries()).map(([path, b]) => ({
        path,
        total: b.total,
        failed: b.failed,
        failure_rate: b.total === 0 ? 0 : b.failed / b.total,
    }));
}

function matchesThreshold(g: Grouped): boolean {
    if (g.total < MIN_INVOCATIONS) return false;
    const threshold = TOLERANT_PATHS.has(g.path) ? TOLERANT_THRESHOLD : FAILURE_RATE_THRESHOLD;
    return g.failure_rate >= threshold;
}

// ---------- Notify ----------

async function notify(groups: Grouped[]) {
    const lines = [
        `🚨 **Windmill workflow failures** (last ${LOOKBACK_MIN} min)`,
        "",
    ];
    for (const g of groups) {
        const pct = Math.round(g.failure_rate * 100);
        lines.push(`- \`${g.path.replace(/^f\/lovenet\//, "")}\` — ${g.failed}/${g.total} failed (${pct}%)`);
    }
    lines.push("");
    lines.push("Inspect: `wmill flows runs` or Windmill UI → Jobs page");

    await Promise.allSettled([
        publishNtfy({
            topic: "alerts",
            title: `🚨 Windmill workflows failing: ${groups.length}`,
            message: lines.join("\n"),
            priority: 4,
            tags: ["warning", "windmill"],
        }),
        postZulipDM(lines.join("\n")),
    ]);
}

async function publishNtfy(args: {
    topic: string;
    title: string;
    message: string;
    priority: number;
    tags: string[];
}) {
    const url = Deno.env.get("NTFY_URL") ?? "https://ntfy.thesteamedcrab.com";
    const token = Deno.env.get("NTFY_WRITE_TOKEN");
    if (!token) throw new Error("NTFY_WRITE_TOKEN env not set");
    const r = await fetch(url, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify(args),
        signal: AbortSignal.timeout(15_000),
    });
    return { status: r.status, ok: r.ok };
}

async function postZulipDM(content: string) {
    const email = Deno.env.get("ZULIP_BOT_EMAIL");
    const apiKey = Deno.env.get("ZULIP_BOT_API_KEY");
    if (!email || !apiKey) throw new Error("ZULIP_BOT_EMAIL / ZULIP_BOT_API_KEY env not set");
    const robId = parseInt(Deno.env.get("ROB_ZULIP_USER_ID") ?? "8", 10);
    const zulipApiUrl = Deno.env.get("ZULIP_API_URL") ?? "http://zulip.collab.svc.cluster.local";
    const zulipHostHeader = Deno.env.get("ZULIP_HOST_HEADER")
        ?? `chat.${Deno.env.get("SECRET_DOMAIN") ?? "thesteamedcrab.com"}`;
    const auth = "Basic " + btoa(`${email}:${apiKey}`);
    const form = new URLSearchParams({ type: "private", to: `[${robId}]`, content });
    const r = await fetch(`${zulipApiUrl}/api/v1/messages`, {
        method: "POST",
        headers: {
            Authorization: auth,
            "Content-Type": "application/x-www-form-urlencoded",
            Host: zulipHostHeader,
        },
        body: form,
        signal: AbortSignal.timeout(15_000),
    });
    return { status: r.status, ok: r.ok };
}
