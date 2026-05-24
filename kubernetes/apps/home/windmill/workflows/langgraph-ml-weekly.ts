// Cron: weekly, Saturday 02:00 America/New_York (routine maintenance window).
//
// Fires the `ml-operator` agent on a weekly Class-A ML / inference
// health sweep. GPU placement + utilization across the P40 and Spark,
// Ollama model-load fitness, Frigate detector residency, Immich CLIP
// + reranker resource posture. Pure surveillance — operator drafts,
// operator (Rob) decides.
//
// Class A only. Any Class C action (model swap, GPU scheduling
// change, helm value bump) routes through errand-runner.
//
// Pinned `target_agent: "ml-operator"`.
//
// Result lands in Zulip stream #digests, topic `ml-health-YYYY-Www`.

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
const POLL_TIMEOUT_MS = 900_000;

function lgaHeaders(): Record<string, string> {
    const tok = Deno.env.get("HAI_CLI_TOKEN");
    return tok ? { Authorization: `Bearer ${tok}` } : {};
}

function isoWeek(d: Date): { year: number; week: number } {
    const utc = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
    const dayNum = utc.getUTCDay() || 7;
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
    const client_task_id = `ml-health-${weekStr}`;

    const promptBody = [
        "WEEKLY ML / INFERENCE HEALTH SWEEP — Class A analysis only.",
        "",
        "Walk the local-inference stack and report drift. Anything",
        "you'd recommend changing, list as Class A findings — do NOT",
        "escalate to Class C/D in this run.",
        "",
        "Coverage:",
        "  1. GPU utilization 7-day trend: P40 (worker8) and Spark",
        "     (GB10). Any sustained idle (<5% over 6h) OR sustained",
        "     saturation (>90% over 6h) worth flagging?",
        "  2. Ollama model-load fitness: which models are resident",
        "     on each Ollama instance? Any swap thrashing visible in",
        "     ollama logs / load-time metrics?",
        "  3. DCGM exporter health on Spark: GR_ENGINE_ACTIVE +",
        "     FB_USED still broken on GB10 per known limitation —",
        "     check POWER_USAGE as proxy is being scraped.",
        "  4. Frigate detector: throughput, queue depth, drops.",
        "     Any model files needing retraining (low confidence",
        "     events, false positives in recent clips)?",
        "  5. Immich ML: CLIP + face-detect queue depths. Backlog?",
        "  6. langgraph-agents fleet: queue depth, dispatch latency,",
        "     DLQ size, cost-cap state. Any agent never being",
        "     invoked vs trigger expectation?",
        "  7. HolmesGPT: alert-to-rootCause success rate over the",
        "     last week (per the prompt surgery PR #12016).",
        "",
        "Output: ranked findings (highest-impact first), each with",
        "evidence + one-sentence rationale + recommended next step.",
        "",
        "Reference: docs/src/ai_architecture.md, gpu-routing.md.",
    ].join("\n");

    const lgResp = await fetch(`${LG_BASE}/inbox`, {
        method: "POST",
        headers: { ...lgaHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({
            task_id: client_task_id,
            source: "scheduled",
            target_agent: "ml-operator",
            content: promptBody,
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

    const output = await pollForOutput(queue_task_id);

    const content = output ||
        `Weekly ML health sweep complete; ml-operator wrote ` +
            `findings to vault. (No prose summary returned; check ` +
            `vault/reports/ml-health-${weekStr}.md.)`;

    const email = Deno.env.get("ZULIP_BOT_EMAIL");
    const apiKey = Deno.env.get("ZULIP_BOT_API_KEY");
    if (!email || !apiKey) throw new Error("ZULIP_BOT_EMAIL / ZULIP_BOT_API_KEY env not set");
    const auth = "Basic " + btoa(`${email}:${apiKey}`);
    const zulipApiUrl = Deno.env.get("ZULIP_API_URL") ?? "http://zulip.collab.svc.cluster.local";
    const form = new URLSearchParams({
        type: "stream",
        to: "digests",
        topic: `ml-health-${weekStr}`,
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
