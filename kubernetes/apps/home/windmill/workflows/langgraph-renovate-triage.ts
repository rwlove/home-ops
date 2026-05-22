// Agent-driven Renovate PR triage. Hourly (on the hour).
//
// Hourly is aggressive — 24 LLM calls/day on Spark qwen2.5:32b plus
// 24 GitHub API calls (well under anonymous 60/h limit). To avoid
// 24 redundant Zulip DMs per day, we track the prior run's verdict
// hash in Windmill `setState/getState` and only DM Rob when the
// verdict text changes. Most hours produce no DM.
//
// Flow:
//   1. Fetch open PRs from rwlove/home-ops (anonymous GitHub API)
//   2. Build a single prompt summarizing all PRs (one LLM call,
//      avoids multi-task fan-out)
//   3. POST to langgraph /inbox; triager routes to homelab-engineer
//      (cluster context). homelab-engineer runs on Spark
//      qwen2.5:32b — typical wall time 3-5 min for ~5 PRs.
//   4. Poll /admin/tasks/<id> until done (≤ 20 min budget)
//   5. Compare verdict hash to prior run. If changed, DM Rob via
//      Zulip; otherwise return `{ skip: true, reason: "no change" }`
//      so the Windmill failure-watcher and Jobs UI both see a
//      successful no-op.
//
// Per HOMELAB-SPEC Layer 3 sourcing principles — network-local
// execution (Spark local model, no Claude API spend), project
// features over bespoke glue (uses the existing /inbox path that
// triager already routes from), fewer moving parts (one workflow,
// no new image/cron/RBAC). Replacement for the daily Claude-API-
// driven `claude-runner-pr-triage` CronJob; run in parallel until
// quality is verified, then retire the Claude path.

type GitHubPR = {
    number: number;
    title: string;
    body: string | null;
    user?: { login: string };
    labels?: Array<{ name: string }>;
    base?: { ref: string };
    head?: { ref: string };
    draft?: boolean;
    mergeable_state?: string;
    html_url: string;
};

import * as wmill from "npm:windmill-client@1.527.0";

const LG_BASE = "http://langgraph-agents.ai.svc.cluster.local:8765";
const REPO = "rwlove/home-ops";

function lgaHeaders(): Record<string, string> {
    const tok = Deno.env.get("HAI_CLI_TOKEN");
    return tok ? { Authorization: `Bearer ${tok}` } : {};
}

// SHA-256 of the verdict text — used as a cheap "did anything change"
// signal between hourly runs. We don't need a cryptographic property;
// we just need a stable digest that changes when any character of the
// verdict list changes.
async function hashString(s: string): Promise<string> {
    const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
    return Array.from(new Uint8Array(buf))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
}

export async function main() {
    // 1. Fetch open PRs
    const prs = await fetchOpenPRs();
    if (prs.length === 0) {
        return { skip: true, reason: "no open PRs" };
    }

    // 2. Build prompt
    const ask = buildPrompt(prs);

    // 3. POST to /inbox — hour-granular task id so each hour is a
    //    fresh task (the langgraph dedup window enforces a 1h TTL on
    //    idempotency_key anyway, so daily-granular keys would
    //    silently drop the 2nd+ runs per day).
    const taskId = `renovate-triage-${new Date().toISOString().slice(0, 13)}`;
    const inboxResp = await fetch(`${LG_BASE}/inbox`, {
        method: "POST",
        headers: { ...lgaHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({
            task_id: taskId,
            source: "cli",
            user: "rob",
            content: ask,
            idempotency_key: taskId, // one triage per day
        }),
        signal: AbortSignal.timeout(15_000),
    });
    if (!inboxResp.ok) {
        const body = await inboxResp.text().catch(() => "");
        return { error: `/inbox returned ${inboxResp.status}`, body: body.slice(0, 400) };
    }
    const _accepted = await inboxResp.json();

    // 4. Poll
    const output = await pollForOutput(taskId);
    if (!output) {
        return { error: "agent task didn't complete within budget", task_id: taskId };
    }

    // 5. Compare to previous hour. Only DM on change.
    const verdictHash = await hashString(output);
    const prevHash = (await wmill.getState()) as string | null;
    if (prevHash === verdictHash) {
        return {
            task_id: taskId,
            pr_count: prs.length,
            skip: true,
            reason: "verdict unchanged from prior hour",
        };
    }
    await wmill.setState(verdictHash);

    await postZulip([
        `**Renovate triage — ${prs.length} open PR(s) at ${REPO}**`,
        "",
        output,
        "",
        `_Triaged by langgraph homelab-engineer. Task: \`${taskId}\`._`,
    ].join("\n"));

    return {
        task_id: taskId,
        pr_count: prs.length,
        output_chars: output.length,
        verdict_changed: true,
    };
}

// ---------- GitHub ----------

async function fetchOpenPRs(): Promise<GitHubPR[]> {
    // Anonymous GitHub API. The repo is public, so list+show work
    // without a PAT. We fetch the summary view (no per-PR detail
    // calls) — the agent gets enough from title + body + labels.
    const r = await fetch(
        `https://api.github.com/repos/${REPO}/pulls?state=open&per_page=30`,
        {
            headers: { Accept: "application/vnd.github+json" },
            signal: AbortSignal.timeout(15_000),
        },
    );
    if (!r.ok) {
        throw new Error(`GitHub API returned ${r.status}`);
    }
    const data = (await r.json()) as GitHubPR[];
    // Skip drafts — they're not ready for triage.
    return data.filter((pr) => !pr.draft);
}

function buildPrompt(prs: GitHubPR[]): string {
    const lines = [
        `Triage these ${prs.length} open PRs at ${REPO}. For each, give a`,
        `2-line verdict in this exact format:`,
        ``,
        `  PR #N: <merge|wait|reject> — <one-line rationale>`,
        ``,
        `Criteria:`,
        `- merge = Renovate version bumps with no breaking changes`,
        `- wait  = Renovate bumps with unclear release notes, or PRs`,
        `          needing human review of scope/risk`,
        `- reject = breaking changes, suspicious diffs, or anything`,
        `          that requires a config change before merging`,
        ``,
        `Be terse. Do not call tools — respond in prose only.`,
        ``,
        `=== Open PRs ===`,
        ``,
    ];
    for (const pr of prs) {
        const labels = (pr.labels ?? []).map((l) => l.name).join(", ") || "(none)";
        const bodyExcerpt = (pr.body ?? "").slice(0, 600).replace(/\r?\n+/g, " ");
        lines.push(`#${pr.number}: ${pr.title}`);
        lines.push(`  author: ${pr.user?.login ?? "?"}  labels: ${labels}`);
        lines.push(`  url: ${pr.html_url}`);
        if (bodyExcerpt) lines.push(`  body: ${bodyExcerpt}`);
        lines.push("");
    }
    return lines.join("\n");
}

// ---------- Langgraph poll ----------

const POLL_INTERVAL_MS = 10_000;
const POLL_TIMEOUT_MS = 1_200_000; // 20 min

async function pollForOutput(taskId: string): Promise<string | null> {
    const deadline = Date.now() + POLL_TIMEOUT_MS;
    while (Date.now() < deadline) {
        await sleep(POLL_INTERVAL_MS);
        const r = await fetch(
            `${LG_BASE}/admin/tasks/${encodeURIComponent(taskId)}`,
            { headers: lgaHeaders(), signal: AbortSignal.timeout(15_000) },
        );
        if (!r.ok) {
            if (r.status === 404) continue;
            throw new Error(`/admin/tasks returned ${r.status}`);
        }
        const j = await r.json();
        const status = j?.queue?.status;
        if (status === "done") {
            return (j?.queue?.result?.output as string | undefined) ?? null;
        }
        if (status === "failed" || status === "dlq") {
            return null;
        }
        // pending / claimed — keep polling
    }
    return null;
}

function sleep(ms: number): Promise<void> {
    return new Promise((r) => setTimeout(r, ms));
}

// ---------- Zulip ----------

async function postZulip(content: string) {
    const email = Deno.env.get("ZULIP_BOT_EMAIL");
    const apiKey = Deno.env.get("ZULIP_BOT_API_KEY");
    if (!email || !apiKey) throw new Error("ZULIP_BOT_EMAIL / ZULIP_BOT_API_KEY env not set");
    const robId = parseInt(Deno.env.get("ROB_ZULIP_USER_ID") ?? "8", 10);
    const zulipApiUrl = Deno.env.get("ZULIP_API_URL") ?? "http://zulip.collab.svc.cluster.local";
    const auth = "Basic " + btoa(`${email}:${apiKey}`);
    const form = new URLSearchParams({ type: "private", to: `[${robId}]`, content });
    const r = await fetch(`${zulipApiUrl}/api/v1/messages`, {
        method: "POST",
        headers: {
            Authorization: auth,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        body: form,
        signal: AbortSignal.timeout(30_000),
    });
    return { status: r.status, ok: r.ok };
}
