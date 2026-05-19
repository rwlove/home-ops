// Triggered by langgraph-agents when a task pauses for approval.
//
// Posts a formatted message into Zulip stream #approvals (so Rob can
// react with 👍 / 👎 / ⏸️), plus a tier-1 Pushover for attention.
//
// Replaces the n8n flow "LangGraph → Approval post to Zulip".

type ApprovalRequest = {
    task_id: string;
    paused_for: {
        approval_request: {
            proposed_by: string;
            action_class: string;
            target: string;
            payload_summary: string;
            undo_path?: string;
            cost_estimate_usd?: number;
        };
    };
};

export async function main(body: ApprovalRequest) {
    const r = body.paused_for.approval_request;
    const topic = `${body.task_id} — Class ${r.action_class}: ${r.target}`;
    const content = [
        `**Task:** \`${body.task_id}\``,
        `**Proposed by:** ${r.proposed_by}`,
        `**Action class:** ${r.action_class}`,
        `**Target:** \`${r.target}\``,
        `**Summary:** ${r.payload_summary}`,
        `**Undo path:** ${r.undo_path ?? "_(none — will escalate to Class D)_"}`,
        `**Cost estimate:** $${r.cost_estimate_usd ?? 0}`,
        "",
        "Reply with one of (Zulip outgoing webhooks fire on @-mentions, not reactions):",
        "  @**Approval Receiver** approve",
        "  @**Approval Receiver** reject",
        "  @**Approval Receiver** defer",
    ].join("\n");

    // Post to Zulip via REST as the n8n-bot user.
    const email = Deno.env.get("ZULIP_BOT_EMAIL");
    const apiKey = Deno.env.get("ZULIP_BOT_API_KEY");
    if (!email || !apiKey) {
        throw new Error("ZULIP_BOT_EMAIL / ZULIP_BOT_API_KEY env not set");
    }
    const auth = "Basic " + btoa(`${email}:${apiKey}`);
    const zulipHost = Deno.env.get("ZULIP_HOST") ?? "chat.thesteamedcrab.com";
    const form = new URLSearchParams({
        type: "stream",
        to: "approvals",
        topic,
        content,
    });
    const zr = await fetch(`https://${zulipHost}/api/v1/messages`, {
        method: "POST",
        headers: {
            Authorization: auth,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        body: form,
        signal: AbortSignal.timeout(30_000),
    });
    const zResult = await zr.json().catch(() => ({}));

    // Tier-1 Pushover.
    const pushoverResp = await sendPushover({
        title: `🔔 Approval needed: Class ${r.action_class}`,
        message: `${r.target}\n${r.payload_summary}\n\nReact in Zulip: chat.${
            Deno.env.get("ZULIP_HOST")?.replace(/^chat\./, "") ?? "thesteamedcrab.com"
        }`,
        priority: 1,
        sound: "gamelan",
    });

    return { task_id: body.task_id, topic, zulip: zResult.result ?? zResult, pushover: pushoverResp };
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
    return { status: r.status };
}
