// Cron: weekly, Mondays at 09:00 America/New_York.
//
// Implements the `upstream-watcher` mode (HOMELAB-SPEC Layer 4 +
// `.agents/skills/upstream-watcher.md` + `.agents/instructions/workarounds.md`).
//
// 1. Fetch open `workaround`-labeled issues on rwlove/home-ops.
// 2. For each tracking issue, find the upstream item to watch, in order:
//    a. an explicit `Watch: <url>` / `Watch: none` line (override — use it
//       when the body cites several links, a closed "closest match", or
//       has no upstream at all);
//    b. an `Upstream: <url>` line;
//    c. the first GitHub issue/PR link under a `## …Upstream…` heading.
// 3. Fetch the upstream issue/PR state via the GitHub REST API.
// 4. Flag for removal only when the gate is genuinely met: a merged PR, or
//    an issue closed as COMPLETED. An issue closed NOT_PLANNED means the fix
//    is never coming — the workaround is permanent, not retireable.
// 5. Email a summary (via in-cluster smtp-relay) if any removals are due.
//
// Anonymous GitHub API access (60 req/hr) is sufficient for the weekly
// cadence + the small number of workarounds expected.

import * as net from "node:net";

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
    upstream_state:
        | "open"
        | "closed"
        | "merged"
        | "not_planned"
        | "none"
        | "unknown"
        | "unparseable";
    removable: boolean;
    note?: string;
};

const HOME_OPS = "rwlove/home-ops";
const GH_BASE = "https://api.github.com";
const TIMEOUT_MS = 30_000;
const WATCH_RE = /^\s*[Ww]atch:\s*(none|https?:\/\/[^\s)>\]]+)/m;
const UPSTREAM_RE = /^\s*[Uu]pstream:\s*(https?:\/\/[^\s)>\]]+)/m;
const GH_ITEM_RE =
    /https?:\/\/github\.com\/[^/\s]+\/[^/\s]+\/(?:issues|pull)\/\d+/;

// dry_run: check and return results but never send the email (for testing;
// the weekly schedule passes no args, so it always notifies).
export async function main(dry_run: boolean = false) {
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
    if (removable.length > 0 && !dry_run) {
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

    const found = findUpstream(tracking.body ?? "");
    if (found === "none") {
        return {
            ...base,
            upstream_state: "none",
            removable: false,
            note: "`Watch: none` — no upstream item gates this workaround",
        };
    }
    if (!found) {
        return {
            ...base,
            upstream_state: "unparseable",
            removable: false,
            note: "no `Watch:`/`Upstream:` line and no GitHub link under an Upstream heading",
        };
    }
    const upstreamUrl = found;

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
            note: merged
                ? "upstream PR merged — confirm it is in a release we deploy before removing"
                : undefined,
        };
    }

    if (upstream.state === "closed" && upstream.state_reason === "not_planned") {
        return {
            ...base,
            upstream_url: upstreamUrl,
            upstream_state: "not_planned",
            removable: false,
            note: "upstream closed NOT_PLANNED — fix is not coming; re-anchor or set `Watch:`",
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

// Returns the URL to watch, "none" for an explicit `Watch: none`, or null.
function findUpstream(body: string): string | "none" | null {
    const w = body.match(WATCH_RE);
    if (w) return w[1].toLowerCase() === "none" ? "none" : w[1];
    const u = body.match(UPSTREAM_RE);
    if (u) return u[1];
    // First GitHub issue/PR link inside a markdown section whose heading
    // mentions "upstream", up to the next heading.
    const lines = body.split(/\r?\n/);
    for (let i = 0; i < lines.length; i++) {
        if (!/^#{1,6}\s.*upstream/i.test(lines[i])) continue;
        for (let j = i + 1; j < lines.length && !/^#{1,6}\s/.test(lines[j]); j++) {
            const m = lines[j].match(GH_ITEM_RE);
            if (m) return m[0];
        }
    }
    return null;
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
    await sendEmail(
        `🔧 ${removable.length} workaround(s) ready to retire`,
        lines.join("\n"),
    ).catch(() => {});
}

// ---------- Email (in-cluster smtp-relay -> Mailgun) ----------
//
// Raw SMTP over plaintext to smtp-relay:2525 (maddy, `tls off`, no auth
// on submission — it relays everything out via Mailgun). Kept
// self-contained per the workflows "no shared modules" convention.

async function sendEmail(subject: string, body: string) {
    const host = process.env.SMTP_HOST ?? "smtp-relay.home.svc.cluster.local";
    const port = parseInt(process.env.SMTP_PORT ?? "2525", 10);
    const from = process.env.NOTIFY_EMAIL_FROM;
    const to = process.env.NOTIFY_EMAIL_TO;
    if (!from) throw new Error("NOTIFY_EMAIL_FROM env not set");
    if (!to) throw new Error("NOTIFY_EMAIL_TO env not set");

    // node:net (Bun implements it natively) in place of the old
    // Deno.connect. Incoming bytes are buffered by the "data" handler;
    // reply() waits until the buffer holds one complete SMTP reply.
    const sock = net.createConnection({ host, port });
    sock.setEncoding("utf8");
    let rbuf = "";
    let closed = false;
    let sockErr: Error | undefined;
    let wake: (() => void) | undefined;
    const poke = () => {
        const w = wake;
        wake = undefined;
        w?.();
    };
    sock.on("data", (chunk: string) => {
        rbuf += chunk;
        poke();
    });
    sock.on("close", () => {
        closed = true;
        poke();
    });
    sock.on("error", (e: Error) => {
        sockErr = e;
        closed = true;
        poke();
    });

    // Read one full SMTP reply. Multi-line replies use "NNN-" for
    // continuation lines and "NNN " (space) on the final line.
    async function reply(): Promise<{ code: number; text: string }> {
        while (true) {
            const last = rbuf.split(/\r?\n/).filter((l) => l.length > 0).at(-1) ?? "";
            if (/^\d{3} /.test(last)) break;
            if (closed) {
                if (sockErr) throw sockErr;
                break;
            }
            await new Promise<void>((r) => {
                wake = r;
            });
        }
        const acc = rbuf;
        rbuf = "";
        return { code: parseInt(acc.slice(0, 3), 10) || 0, text: acc.trim() };
    }
    async function cmd(line: string, expect: number) {
        await new Promise<void>((resolve, reject) => {
            sock.write(line + "\r\n", (err) => (err ? reject(err) : resolve()));
        });
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
        sock.destroy();
    }
}
