// Periodic self-check: detect Windmill scripts that are failing at a
// high rate and notify Rob via email (in-cluster smtp-relay → Mailgun).
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
// Partial mitigation: the BlackboxProbe on windmill /api/version
// catches the "Windmill is down" case. It does NOT catch "Windmill is
// up but this script is erroring" — the langgraph-dlq-watcher Pushover
// that used to cover that was removed with the fleet (2026-07-06), so
// today that case surfaces only on manual `wmill flows runs` inspection.

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
// Currently empty: the only entry was langgraph-dlq-watcher, removed
// with the fleet (2026-07-06). Kept as a policy hook — add a path here
// rather than raising FAILURE_RATE_THRESHOLD for everything.
const TOLERANT_PATHS = new Set<string>([]);
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
    // Windmill's API path is `/jobs/completed/list`, NOT `/jobs/list_completed`
    // (the latter 404s). The `/jobs/list` endpoint exists but mixes running
    // + completed; completed/list filters to terminal jobs and accepts the
    // `started_after` filter we need.
    const url = `${WMILL_BASE}/api/w/${WORKSPACE}/jobs/completed/list`
        + `?per_page=500&started_after=${encodeURIComponent(startedAfter)}`
        + `&job_kinds=script`;
    const r = await fetch(url, {
        headers: { Authorization: `Bearer ${token}` },
        signal: AbortSignal.timeout(15_000),
    });
    if (!r.ok) {
        throw new Error(`jobs/completed/list returned ${r.status}`);
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
        `Windmill workflow failures (last ${LOOKBACK_MIN} min):`,
        "",
    ];
    for (const g of groups) {
        const pct = Math.round(g.failure_rate * 100);
        lines.push(`- ${g.path.replace(/^f\/lovenet\//, "")} — ${g.failed}/${g.total} failed (${pct}%)`);
    }
    lines.push("");
    lines.push("Inspect: `wmill flows runs` or Windmill UI -> Jobs page");

    await sendEmail(`🚨 Windmill workflows failing: ${groups.length}`, lines.join("\n"));
}

// ---------- Email (in-cluster smtp-relay -> Mailgun) ----------
//
// Raw SMTP over plaintext to smtp-relay:2525 (maddy, `tls off`, no auth
// on submission — it relays everything out via Mailgun). Kept
// self-contained per the workflows "no shared modules" convention.

async function sendEmail(subject: string, body: string) {
    const host = Deno.env.get("SMTP_HOST") ?? "smtp-relay.home.svc.cluster.local";
    const port = parseInt(Deno.env.get("SMTP_PORT") ?? "2525", 10);
    const from = Deno.env.get("NOTIFY_EMAIL_FROM");
    const to = Deno.env.get("NOTIFY_EMAIL_TO");
    if (!from) throw new Error("NOTIFY_EMAIL_FROM env not set");
    if (!to) throw new Error("NOTIFY_EMAIL_TO env not set");

    const conn = await Deno.connect({ hostname: host, port });
    const enc = new TextEncoder();
    const dec = new TextDecoder();
    const rbuf = new Uint8Array(4096);

    // Read one full SMTP reply. Multi-line replies use "NNN-" for
    // continuation lines and "NNN " (space) on the final line.
    async function reply(): Promise<{ code: number; text: string }> {
        let acc = "";
        while (true) {
            const n = await conn.read(rbuf);
            if (n === null) break;
            acc += dec.decode(rbuf.subarray(0, n));
            const last = acc.split(/\r?\n/).filter((l) => l.length > 0).at(-1) ?? "";
            if (/^\d{3} /.test(last)) break;
        }
        return { code: parseInt(acc.slice(0, 3), 10) || 0, text: acc.trim() };
    }
    async function cmd(line: string, expect: number) {
        await conn.write(enc.encode(line + "\r\n"));
        const { code, text } = await reply();
        if (code !== expect) {
            throw new Error(`SMTP ${line.split(/\s/)[0]}: expected ${expect}, got ${text}`);
        }
    }

    try {
        const greet = await reply(); // 220 banner
        if (greet.code !== 220) throw new Error(`SMTP greeting: ${greet.text}`);
        await cmd(`EHLO windmill`, 250);
        await cmd(`MAIL FROM:<${from}>`, 250);
        await cmd(`RCPT TO:<${to}>`, 250);
        await cmd(`DATA`, 354);
        const msg =
            `From: ${from}\r\n` +
            `To: ${to}\r\n` +
            `Subject: ${subject}\r\n` +
            `Content-Type: text/plain; charset=utf-8\r\n` +
            `\r\n` +
            body.replace(/\r?\n/g, "\r\n").replace(/^\./gm, "..") +
            `\r\n.`;
        await cmd(msg, 250);
        await cmd(`QUIT`, 221);
    } finally {
        conn.close();
    }
}
