// Cron: every 4 hours.
//
// Polls /admin/costs/today on langgraph-agents and Pushovers if we're
// approaching (≥80%) or have crossed (≥100%) the daily cap.
//
// Endpoint may 404 until Langfuse phase is online — we degrade
// gracefully.
//
// Replaces the n8n flow "LangGraph → Cost cap watcher".

type CostsResponse = {
    spent_usd?: number;
    daily_cap_usd?: number;
    per_agent?: Record<string, number>;
};

export async function main() {
    const r = await fetch(
        "http://langgraph-agents.ai.svc.cluster.local:8765/admin/costs/today",
        { signal: AbortSignal.timeout(30_000) },
    );
    if (!r.ok) {
        return { skip: true, reason: `endpoint returned ${r.status}` };
    }
    const body: CostsResponse = (await r.json().catch(() => ({}))) as CostsResponse;
    const spent = Number(body.spent_usd ?? 0);
    const cap = Number(body.daily_cap_usd ?? 30);
    const pct = cap > 0 ? Math.round((spent / cap) * 100) : 0;

    if (pct >= 100) {
        await sendPushover({
            title: `💥 Daily cost cap HIT ($${spent} / $${cap})`,
            message:
                "langgraph-agents will refuse Claude-API-eligible specialists for the rest of the day.",
            priority: 1,
            sound: "siren",
        });
        return { tier: "hit", spent, cap, pct };
    }
    if (pct >= 80) {
        await sendPushover({
            title: `⚠️ Daily cost cap 80%+ ($${spent} / $${cap})`,
            message: "Approaching the daily limit. Consider deferring non-urgent Claude-API tasks.",
            priority: 0,
            sound: "intermission",
        });
        return { tier: "warn", spent, cap, pct };
    }
    return { tier: "ok", spent, cap, pct };
}

async function sendPushover(args: {
    title: string;
    message: string;
    priority: number;
    sound?: string;
}) {
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
