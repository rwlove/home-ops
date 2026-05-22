// Agent-driven Renovate PR triage. Daily at 08:00 ET (before
// claude-runner-pr-triage's 09:00 EDT / 08:00 EST so we can compare
// outputs side-by-side during the migration window).
//
// Flow:
//   1. Fetch open PRs from rwlove/home-ops (anonymous GitHub API —
//      the repo is public; ~5 req/day for ~5 PRs, well under the
//      60 req/h anonymous limit).
//   2. Build a single prompt summarizing all PRs (so we get ONE
//      LLM call instead of N; reduces latency and avoids
//      multi-task fan-out complexity).
//   3. POST to langgraph /inbox as the `triager` source=cli — the
//      triager routes to `homelab-engineer` (cluster context) for
//      verdicts. `homelab-engineer` runs on Spark qwen2.5:32b.
//   4. Poll /admin/tasks/<id> until done (≤ 20 min — agent
//      reasoning over 5 PRs at 32b is ~3-5 min typical).
//   5. DM Rob the verdicts via Zulip.
//
// Per HOMELAB-SPEC Layer 3 sourcing principles, this is the
// "project features over bespoke glue" replacement for the
// Claude-API-driven `claude-runner-pr-triage` CronJob: local
// inference, no per-call cost, uses the existing fleet. Run in
// parallel with claude-runner-pr-triage until quality is verified;
// retire the Claude path after.

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

const LG_BASE = "http://langgraph-agents.ai.svc.cluster.local:8765";
const REPO = "rwlove/home-ops";

function lgaHeaders(): Record<string, string> {
    const tok = Deno.env.get("HAI_CLI_TOKEN");
    return tok ? { Authorization: `Bearer ${tok}` } : {};
}

export async function main() {
    // 1. Fetch open PRs
    const prs = await fetchOpenPRs();
    if (prs.length === 0) {
        return { skip: true, reason: "no open PRs" };
    }

    // 2. Build prompt
    const ask = buildPrompt(prs);

    // 3. POST to /inbox
    const taskId = `renovate-triage-${new Date().toISOString().slice(0, 10)}`;
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

    // 5. DM
    await postZulip([
        `**Daily Renovate triage — ${prs.length} open PR(s) at ${REPO}**`,
        "",
        output,
        "",
        `_Triaged by langgraph homelab-engineer. Task: \`${taskId}\`._`,
    ].join("\n"));

    return { task_id: taskId, pr_count: prs.length, output_chars: output.length };
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
