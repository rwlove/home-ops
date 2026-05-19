// Cron: every 5 minutes.
//
// Buckets interrupted (paused-for-user) tasks by age and acts:
//   30 min  → tier-1 Pushover escalation
//   4 hr    → POST /admin/tasks/<id>/timeout-tier {tier: "4h"} (mark cold)
//   7 day   → POST /admin/tasks/<id>/cancel (auto-cancel)
//
// Replaces the n8n flow "LangGraph → Awaiting-user sweep".

type Task = {
    task_id: string;
    interrupts?: unknown[];
    awaiting_user_since?: string | null;
    target_agent?: string;
};

const MIN_30 = 30 * 60 * 1000;
const HR_4 = 4 * 60 * 60 * 1000;
const DAY_7 = 7 * 24 * 60 * 60 * 1000;

export async function main() {
    const r = await fetch(
        "http://langgraph-agents.ai.svc.cluster.local:8765/admin/tasks",
        { signal: AbortSignal.timeout(30_000) },
    );
    if (!r.ok) {
        return { skip: true, reason: `GET /admin/tasks → ${r.status}` };
    }
    const tasks: Task[] = (await r.json().catch(() => [])) as Task[];
    const paused = (Array.isArray(tasks) ? tasks : []).filter(
        (t) => t.interrupts && t.interrupts.length > 0,
    );

    const now = Date.now();
    const results: Array<{ task_id: string; tier: string; action: string; ok: boolean }> = [];
    for (const t of paused) {
        const since = t.awaiting_user_since ? new Date(t.awaiting_user_since).getTime() : null;
        if (!since) continue;
        const age = now - since;
        let tier: "30min" | "4h" | "7d" | null = null;
        if (age > DAY_7) tier = "7d";
        else if (age > HR_4) tier = "4h";
        else if (age > MIN_30) tier = "30min";
        if (!tier) continue;
        const out = await handle(t, tier, age);
        results.push({ task_id: t.task_id, tier, ...out });
    }
    return { paused_count: paused.length, acted_on: results };
}

async function handle(t: Task, tier: "30min" | "4h" | "7d", age: number) {
    if (tier === "30min") {
        const ok = await sendPushover({
            title: `⏰ Task awaiting you (30m): ${t.task_id}`,
            message: `Agent ${t.target_agent ?? "unknown"} is paused, age ${
                Math.round(age / 60000)
            }m. Reply in Zulip #approvals.`,
            priority: 1,
            sound: "gamelan",
        });
        return { action: "pushover", ok };
    }
    if (tier === "4h") {
        const r = await fetch(
            `http://langgraph-agents.ai.svc.cluster.local:8765/admin/tasks/${
                encodeURIComponent(t.task_id)
            }/timeout-tier`,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ tier: "4h" }),
                signal: AbortSignal.timeout(30_000),
            },
        );
        return { action: "mark-cold", ok: r.ok };
    }
    // 7d → cancel
    const r = await fetch(
        `http://langgraph-agents.ai.svc.cluster.local:8765/admin/tasks/${
            encodeURIComponent(t.task_id)
        }/cancel`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ reason: "7-day timeout; auto-cancelled by awaiting-user sweep" }),
            signal: AbortSignal.timeout(30_000),
        },
    );
    return { action: "cancel", ok: r.ok };
}

async function sendPushover(args: {
    title: string;
    message: string;
    priority: number;
    sound?: string;
}): Promise<boolean> {
    const token = Deno.env.get("PUSHOVER_APP_TOKEN");
    const user = Deno.env.get("PUSHOVER_USER_KEY");
    if (!token || !user) {
        throw new Error("PUSHOVER_APP_TOKEN / PUSHOVER_USER_KEY env not set");
    }
    const form = new URLSearchParams({
        token,
        user,
        title: args.title,
        message: args.message,
        priority: String(args.priority),
    });
    if (args.sound) form.set("sound", args.sound);
    const r = await fetch("https://api.pushover.net/1/messages.json", {
        method: "POST",
        body: form,
        signal: AbortSignal.timeout(30_000),
    });
    return r.ok;
}
