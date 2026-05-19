// Cron: daily at 22:00 America/New_York.
//
// Triggers the reporter agent via POST /inbox on langgraph-agents,
// then posts the digest summary to Zulip stream #digests.
//
// Replaces the n8n flow "LangGraph → Daily digest".

type InboxResp = { task_id?: string; status?: string; output?: string };

export async function main() {
    const date = new Date().toISOString().slice(0, 10);
    const task_id = `digest-${date}`;

    const lgResp = await fetch(
        "http://langgraph-agents.ai.svc.cluster.local:8765/inbox",
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                task_id,
                source: "test",
                content: "Generate today's daily digest from the per-agent activity logs.",
                user: "rob",
            }),
            signal: AbortSignal.timeout(600_000),
        },
    );
    const lg: InboxResp = (await lgResp.json().catch(() => ({}))) as InboxResp;

    const content = lg.output ||
        `Daily digest task complete; see vault/reports/daily-${date}.md`;

    // Post to Zulip stream #digests, topic "daily-YYYY-MM-DD".
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

    return { task_id, langgraph_status: lg.status, zulip: zResult.result ?? zResult };
}
