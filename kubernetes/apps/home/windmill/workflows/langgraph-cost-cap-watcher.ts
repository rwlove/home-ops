// Cron: every 4 hours.
//
// Polls /admin/costs/today on langgraph-agents and ntfys if we're
// approaching (≥80%) or have crossed (≥100%) the daily cap.
//
// Endpoint may 404 until Langfuse phase is online — we degrade gracefully.
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
        await publishNtfy({
            topic: "costs",
            title: `💥 Daily cost cap HIT ($${spent} / $${cap})`,
            message:
                "langgraph-agents will refuse Claude-API-eligible specialists for the rest of the day.",
            priority: 5,
            tags: ["boom"],
        });
        return { tier: "hit", spent, cap, pct };
    }
    if (pct >= 80) {
        await publishNtfy({
            topic: "costs",
            title: `⚠️ Daily cost cap 80%+ ($${spent} / $${cap})`,
            message: "Approaching the daily limit. Consider deferring non-urgent Claude-API tasks.",
            priority: 4,
            tags: ["warning"],
        });
        return { tier: "warn", spent, cap, pct };
    }
    return { tier: "ok", spent, cap, pct };
}

async function publishNtfy(args: {
    topic: string;
    title: string;
    message: string;
    priority?: number;
    tags?: string[];
}) {
    const url = Deno.env.get("NTFY_URL") ?? "https://ntfy.thesteamedcrab.com";
    const token = Deno.env.get("NTFY_WRITE_TOKEN");
    if (!token) throw new Error("NTFY_WRITE_TOKEN env not set");
    const r = await fetch(url, {
        method: "POST",
        headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
        },
        body: JSON.stringify({
            topic: args.topic,
            title: args.title,
            message: args.message,
            priority: args.priority ?? 3,
            tags: args.tags ?? [],
        }),
        signal: AbortSignal.timeout(30_000),
    });
    return { status: r.status, ok: r.ok };
}
