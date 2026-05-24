// Cron: weekly, Saturday 06:00 America/New_York.
//
// Fires the `reviewer` agent on its actual job — vault hygiene:
// aging TODOs, drift findings, dead [[wiki-links]]. The reviewer
// node's docstring (src/agents/nodes/reviewer.py) explicitly names a
// "weekly cadence" contract; this is the trigger that satisfies it.
//
// Pinned via `target_agent: "reviewer"` to bypass the qwen2.5:7b
// triager (same pattern as the historian daily-digest pin) — no
// natural-language prompt that consistently routes 20+ alternatives
// to `reviewer`.
//
// Result lands in Zulip stream #digests, topic
// `vault-hygiene-<ISO-week>`. The body is the reviewer's
// `ReviewerDigest` rendered via the reporter agent (the universal
// final-hop user-facing messenger), so it's already markdown-shaped
// for Zulip.
//
// NOT a PR-code-review cron — the `reviewer` agent in this fleet is
// purpose-built for vault hygiene, not code review. If a PR-review
// surface ever lands, it'll be a separate agent (probably `coder`
// extension or a new `pr-reviewer`).

type InboxResp = { task_id?: string; status?: string };
type AdminTaskResp = {
    task_id?: string;
    queue?: {
        status?: string;
        result?: { output?: string } | null;
        last_error?: string | null;
    };
    checkpointer?: { values?: { output?: string } };
};

const LG_BASE = "http://langgraph-agents.ai.svc.cluster.local:8765";
const POLL_INTERVAL_MS = 5_000;
const POLL_TIMEOUT_MS = 600_000; // 10 min — reviewer walks the vault, can be slow

// HAI_CLI_TOKEN gates /admin/* + /inbox on langgraph-agents (Bearer
// middleware since 0.2.38). Empty header dict when unset (dev mode);
// langgraph-agents server-side falls back to allow-all in that mode.
function lgaHeaders(): Record<string, string> {
    const tok = Deno.env.get("HAI_CLI_TOKEN");
    return tok ? { Authorization: `Bearer ${tok}` } : {};
}

// ISO week number for the Zulip topic. We want all entries for the
// same calendar week to land in the same topic so weekly trends are
// scannable. Standard ISO-8601 week numbering (Mon-start, week 1
// contains the first Thursday).
function isoWeek(d: Date): { year: number; week: number } {
    const utc = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
    const dayNum = utc.getUTCDay() || 7; // Sun=0 → 7 (ISO Mon=1)
    utc.setUTCDate(utc.getUTCDate() + 4 - dayNum);
    const yearStart = new Date(Date.UTC(utc.getUTCFullYear(), 0, 1));
    const weekNum = Math.ceil(
        (((utc.getTime() - yearStart.getTime()) / 86400000) + 1) / 7,
    );
    return { year: utc.getUTCFullYear(), week: weekNum };
}

export async function main() {
    const now = new Date();
    const date = now.toISOString().slice(0, 10);
    const { year, week } = isoWeek(now);
    const weekStr = `${year}-W${String(week).padStart(2, "0")}`;
    const client_task_id = `vault-hygiene-${weekStr}`;

    // Step 1: enqueue the reviewer task. Pinned target_agent so the
    // qwen2.5:7b triager doesn't mis-route across 20 alternatives.
    const lgResp = await fetch(`${LG_BASE}/inbox`, {
        method: "POST",
        headers: { ...lgaHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({
            task_id: client_task_id,
            source: "scheduled",
            target_agent: "reviewer",
            content: (
                "Run weekly vault hygiene sweep: aging TODOs (urgent / " +
                "notable / routine tiers), drift findings (memory vs " +
                "code), dead [[wiki-links]] across the vault. Produce a " +
                "one-paragraph state-of-the-vault summary plus the " +
                "structured findings."
            ),
            user: "rob",
        }),
        signal: AbortSignal.timeout(30_000),
    });
    if (!lgResp.ok) {
        throw new Error(`/inbox enqueue failed: HTTP ${lgResp.status}`);
    }
    const lg: InboxResp = (await lgResp.json().catch(() => ({}))) as InboxResp;
    const queue_task_id = lg.task_id;
    if (!queue_task_id) {
        throw new Error(`/inbox response missing task_id: ${JSON.stringify(lg)}`);
    }

    // Step 2: poll /admin/tasks/<id> until queue.status="done".
    // Same shape as langgraph-daily-digest.ts — the queue worker
    // runs the fleet graph and writes the final output back.
    const output = await pollForOutput(queue_task_id);

    const content = output ||
        `Weekly vault-hygiene sweep complete; reviewer wrote findings ` +
            `to vault. (No prose summary returned; check ` +
            `vault/reports/vault-hygiene-${weekStr}.md.)`;

    // Step 3: post to Zulip stream #digests, topic vault-hygiene-YYYY-Www.
    const email = Deno.env.get("ZULIP_BOT_EMAIL");
    const apiKey = Deno.env.get("ZULIP_BOT_API_KEY");
    if (!email || !apiKey) throw new Error("ZULIP_BOT_EMAIL / ZULIP_BOT_API_KEY env not set");
    const auth = "Basic " + btoa(`${email}:${apiKey}`);
    const zulipApiUrl = Deno.env.get("ZULIP_API_URL") ?? "http://zulip.collab.svc.cluster.local";
    const form = new URLSearchParams({
        type: "stream",
        to: "digests",
        topic: `vault-hygiene-${weekStr}`,
        content,
    });
    const zr = await fetch(`${zulipApiUrl}/api/v1/messages`, {
        method: "POST",
        headers: {
            Authorization: auth,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        body: form,
        signal: AbortSignal.timeout(30_000),
    });
    const zResult = await zr.json().catch(() => ({}));

    return {
        client_task_id,
        queue_task_id,
        week: weekStr,
        date,
        zulip: zResult.result ?? zResult,
    };
}

async function pollForOutput(taskId: string): Promise<string | null> {
    const deadline = Date.now() + POLL_TIMEOUT_MS;
    while (Date.now() < deadline) {
        const r = await fetch(
            `${LG_BASE}/admin/tasks/${encodeURIComponent(taskId)}`,
            { signal: AbortSignal.timeout(15_000), headers: lgaHeaders() },
        );
        if (!r.ok) {
            if (r.status === 404) {
                await sleep(POLL_INTERVAL_MS);
                continue;
            }
            throw new Error(`/admin/tasks/${taskId} returned HTTP ${r.status}`);
        }
        const body = (await r.json().catch(() => ({}))) as AdminTaskResp;
        const status = body.queue?.status;
        if (status === "done") {
            const queued_output = body.queue?.result?.output;
            const cp_output = body.checkpointer?.values?.output;
            return queued_output ?? cp_output ?? null;
        }
        if (body.queue?.last_error) {
            throw new Error(`task ${taskId} failed: ${body.queue.last_error}`);
        }
        await sleep(POLL_INTERVAL_MS);
    }
    throw new Error(`poll timeout waiting for task ${taskId} (${POLL_TIMEOUT_MS}ms)`);
}

function sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
}
