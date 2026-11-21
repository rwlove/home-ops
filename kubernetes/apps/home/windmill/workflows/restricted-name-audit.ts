// Cron: weekly, Mondays at 09:15 America/New_York.
//
// Restricted-name audit for rwlove/home-ops issues, PRs and comments
// (HOMELAB-SPEC Layer 2 #2, `.agents/instructions/data-classification.md`).
//
// The CI scrub job scans tracked *.md files only; nothing else watches the
// narrative people and agents write on GitHub: issue/PR titles and bodies,
// issue comments, and PR review comments. Once posted, a leak persists in
// edit history and title-change timeline events, so the point is to catch
// a new one within a week and fix it while it's still a single revision.
//
// 1. Read the restricted-name pattern from the Windmill secret variable
//    `f/lovenet/restricted_name_regex`. The pattern never lives in this
//    public repo.
// 2. List items created or updated since the last scan: issues and PRs
//    (`/issues?since=`), issue comments, PR review comments, and commits on
//    the default branch. Squash merges copy PR text into commit messages,
//    so a PR-body leak becomes permanent history on merge.
//    Not scanned: PR review *bodies* (`gh pr review -b`). They have no
//    repo-wide list endpoint, and one call per PR would blow the anonymous
//    60 req/hr budget at agent PR volume.
// 3. Skip Renovate only. Its PR titles, commits and dashboards are the
//    documented accepted exception (machine-generated dependency
//    metadata); every other author, including other bots, is scanned.
// 4. Email one itemized list of findings (links and where the match is,
//    never the matched text: the mail relays out via Mailgun). No findings
//    means no email.
// 5. Advance the cursor only after a successful scan and send.
//
// This is the weekly backstop. The merge-time gate on PR titles/bodies is
// a separate CI job (Vikunja #147).
//
// With no stored cursor (first run, or state lost) it scans from
// FIRST_RUN_SINCE rather than silently baselining, so nothing is skipped.
// Pass `since_override` (ISO time) with `dry_run: true` to test detection
// against a past window without emailing or moving the cursor.
//
// Anonymous GitHub API access (60 req/hr) covers a weekly window; a
// rate-limit or HTTP error fails the job, which windmill-failure-watcher
// reports.

import * as net from "node:net";
import * as wmill from "windmill-client@1.527.0";

const GH_BASE = "https://api.github.com";
const REPO = "rwlove/home-ops";
const PATTERN_VAR = "f/lovenet/restricted_name_regex";
const MAX_PAGES = 20;
const FIRST_RUN_SINCE = "2026-11-01T00:00:00Z";
const SKEW_MS = 10 * 60 * 1000;
// More than this many findings in one run most likely means a broken
// pattern (e.g. a stray `|` matching everything), not a real leak wave.
const MAX_SANE_FINDINGS = 75;

type AuditState = { last_scan_at?: string };

type Finding = { url: string; where: string; author: string };

// Renovate in all its forms (GitHub app, self-hosted operator, the old
// rosey bot). Deliberately narrow: other bots are audited.
function isRenovate(login: string | undefined | null): boolean {
    const l = (login ?? "").toLowerCase();
    return l.includes("renovat");
}

async function ghGet(url: string): Promise<any> {
    const res = await fetch(url, {
        headers: { Accept: "application/vnd.github+json", "User-Agent": "lovenet-restricted-name-audit" },
        signal: AbortSignal.timeout(30_000),
    });
    if (!res.ok) throw new Error(`GitHub ${res.status} on ${url.replace(GH_BASE, "")}`);
    return res.json();
}

async function ghList(path: string, since: string): Promise<any[]> {
    const out: any[] = [];
    for (let page = 1; page <= MAX_PAGES; page++) {
        const sep = path.includes("?") ? "&" : "?";
        const url = `${GH_BASE}/repos/${REPO}/${path}${sep}since=${encodeURIComponent(since)}&per_page=100&page=${page}`;
        const items = (await ghGet(url)) as any[];
        out.push(...items);
        if (items.length < 100) return out;
    }
    throw new Error(
        `GitHub ${path}: more than ${MAX_PAGES} pages since ${since} (the cursor fell behind). ` +
            `Recover by running once manually with dry_run=false and since_override set to a recent ISO time.`,
    );
}

export async function main(dry_run: boolean = false, since_override: string = "") {
    const pattern = await wmill.getVariable(PATTERN_VAR);
    if (!pattern) throw new Error(`${PATTERN_VAR} is empty`);
    const re = new RegExp(pattern, "i");

    const state = ((await wmill.getState()) as AuditState | null) ?? {};
    const scanStartedAt = new Date().toISOString();
    const since = since_override ||
        (state.last_scan_at ? new Date(Date.parse(state.last_scan_at) - SKEW_MS).toISOString() : FIRST_RUN_SINCE);

    const [issues, issueComments, reviewComments, commits] = await Promise.all([
        ghList("issues?state=all", since),
        ghList("issues/comments", since),
        ghList("pulls/comments", since),
        ghList("commits", since),
    ]);

    const findings: Finding[] = [];
    for (const i of issues) {
        const author = i.user?.login;
        if (isRenovate(author)) continue;
        const kind = i.pull_request ? "PR" : "issue";
        if (re.test(i.title ?? "")) findings.push({ url: i.html_url, where: `${kind} title`, author });
        if (re.test(i.body ?? "")) findings.push({ url: i.html_url, where: `${kind} body`, author });
    }
    for (const c of issueComments) {
        if (isRenovate(c.user?.login)) continue;
        if (re.test(c.body ?? "")) findings.push({ url: c.html_url, where: "comment", author: c.user?.login });
    }
    for (const c of reviewComments) {
        if (isRenovate(c.user?.login)) continue;
        if (re.test(c.body ?? "")) findings.push({ url: c.html_url, where: "PR review comment", author: c.user?.login });
    }
    for (const c of commits) {
        const author = c.author?.login ?? c.commit?.author?.name;
        if (isRenovate(author) || isRenovate(c.commit?.author?.name)) continue;
        if (re.test(c.commit?.message ?? "")) {
            findings.push({ url: c.html_url, where: "commit message (main history; fixing needs a rewrite)", author });
        }
    }

    if (findings.length > MAX_SANE_FINDINGS) {
        throw new Error(
            `${findings.length} findings exceeds ${MAX_SANE_FINDINGS}: the pattern in ${PATTERN_VAR} is probably broken`,
        );
    }

    const scanned = {
        issues_and_prs: issues.length,
        issue_comments: issueComments.length,
        review_comments: reviewComments.length,
        commits: commits.length,
    };

    if (findings.length > 0 && !dry_run) {
        const lines = findings.map((f) => `• ${f.where} (${f.author}): ${f.url}`);
        await sendEmail(
            `[home-ops] ${findings.length} restricted-name finding(s) in issues/PRs`,
            [
                `Restricted app names (HOMELAB-SPEC L2 #2) appeared in GitHub narrative since ${since}.`,
                `The matched text is deliberately left out of this email.`,
                ``,
                ...lines,
                ``,
                `Next step: rewrite each to generic wording ("the media stack"), then delete the`,
                `old revision in the GitHub UI ("edited" -> revision -> ... -> "Delete revision`,
                `from history"). A changed title leaves a timeline event; an issue may need`,
                `deleting instead. See .agents/instructions/data-classification.md.`,
            ].join("\n"),
        );
    }

    if (!dry_run) await wmill.setState({ last_scan_at: scanStartedAt });
    return { since, scanned, findings };
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
