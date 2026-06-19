// Cron: daily at 22:00 America/New_York.
//
// Triggers the reporter agent via POST /inbox on langgraph-agents,
// polls /admin/tasks/<id> until complete, then posts the digest summary
// to Zulip stream #digests.
//
// Phase 4.M2 cutover: /inbox returns 202 + task_id immediately. The
// langgraph-agents worker handles the graph invocation in the
// background. Poll for completion before posting to Zulip.
//
// Replaces the flow "LangGraph → Daily digest".

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
const POLL_TIMEOUT_MS = 600_000; // 10 min; the digest is the longest /inbox we run

// HAI_CLI_TOKEN gates /admin/* + /inbox on langgraph-agents since 0.2.38
// (PR-C bearer-auth middleware). Inject the token into every call to the
// in-cluster langgraph-agents Service URL. Returns an empty header dict
// when the env is unset (dev / pre-deployment) — server-side falls back
// to allow-all in that mode, so the workflow stays functional.
function lgaHeaders(): Record<string, string> {
    const tok = Deno.env.get("HAI_CLI_TOKEN");
    return tok ? { Authorization: `Bearer ${tok}` } : {};
}

export async function main() {
    const date = new Date().toISOString().slice(0, 10);
    const client_task_id = `digest-${date}`;

    // Step 1: enqueue the digest task. Returns 202 + queue_task_id.
    //
    // `target_agent: "historian"` pins the daily-digest cron to the
    // historian agent (its docstring: "activity log curator + daily/
    // weekly/monthly accomplishment digests"). Without the pin, the
    // qwen2.5:7b triager has to pick "historian" out of 20 alternatives
    // from the prompt "Generate today's daily digest" — high enough
    // mis-routing risk that a single bad routing decision on the
    // 22:00 cron means Rob's daily summary doesn't arrive. Cheap fix.
    const lgResp = await fetch(`${LG_BASE}/inbox`, {
        method: "POST",
        headers: { ...lgaHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({
            task_id: client_task_id,
            source: "scheduled",
            target_agent: "historian",
            content: "Generate today's daily digest from the per-agent activity logs.",
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

    // Step 2: poll /admin/tasks/<id> until queue.status="done" (or timeout).
    const output = await pollForOutput(queue_task_id);

    const content = output ||
        `Daily digest task complete; see vault/reports/daily-${date}.md`;

    // Step 3: post to Zulip stream #digests, topic "daily-YYYY-MM-DD".
    const email = Deno.env.get("ZULIP_BOT_EMAIL");
    const apiKey = Deno.env.get("ZULIP_BOT_API_KEY");
    if (!email || !apiKey) throw new Error("ZULIP_BOT_EMAIL / ZULIP_BOT_API_KEY env not set");
    const auth = "Basic " + btoa(`${email}:${apiKey}`);
    const zulipApiUrl = Deno.env.get("ZULIP_API_URL") ?? "http://zulip.collab.svc.cluster.local";
    const form = new URLSearchParams({
        type: "stream",
        to: "digests",
        topic: `daily-${date}`,
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
        langgraph_status: "done",
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
                // Worker hasn't picked it up yet; back off briefly.
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
