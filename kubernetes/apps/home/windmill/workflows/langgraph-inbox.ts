// Triggered by webhook (Zulip bot or voice intake).
//
// Forwards the inbox payload to langgraph-agents /inbox. If the
// agent pauses (status=paused), runs the approval-post logic
// inline (Zulip post + tier-1 Pushover) so there's no second
// webhook hop, unlike n8n where the flow POSTed back to its own
// /webhook/ URL.
//
// Replaces the n8n flow "LangGraph → Inbox webhook".

type InboxBody = {
    task_id?: string;
    source?: string;
    content: string;
    user?: string;
};

type LgResp = {
    task_id?: string;
    status?: string;
    paused_for?: {
        approval_request?: {
            proposed_by: string;
            action_class: string;
            target: string;
            payload_summary: string;
            undo_path?: string;
            cost_estimate_usd?: number;
        };
    };
};

export async function main(body: InboxBody) {
    const task_id = body.task_id ||
        `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    const source = body.source ?? "voice";
    const content = body.content;
    const user = body.user ?? "rob";

    const r = await fetch("http://langgraph-agents.ai.svc.cluster.local:8765/inbox", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id, source, content, user }),
        signal: AbortSignal.timeout(300_000),
    });
    const lg: LgResp = (await r.json().catch(() => ({}))) as LgResp;

    let approval: unknown = null;
    if (lg.status === "paused" && lg.paused_for?.approval_request) {
        approval = await postApproval(task_id, lg.paused_for.approval_request);
    }
    return { task_id, status: lg.status ?? "unknown", approval };
}

async function postApproval(
    task_id: string,
    r: NonNullable<NonNullable<LgResp["paused_for"]>["approval_request"]>,
) {
    const topic = `${task_id} — Class ${r.action_class}: ${r.target}`;
    const content = [
        `**Task:** \`${task_id}\``,
        `**Proposed by:** ${r.proposed_by}`,
        `**Action class:** ${r.action_class}`,
        `**Target:** \`${r.target}\``,
        `**Summary:** ${r.payload_summary}`,
        `**Undo path:** ${r.undo_path ?? "_(none — will escalate to Class D)_"}`,
        `**Cost estimate:** $${r.cost_estimate_usd ?? 0}`,
        "",
        "React: 👍 approve / 👎 reject / ⏸️ defer 4h",
    ].join("\n");

    const email = Deno.env.get("ZULIP_BOT_EMAIL");
    const apiKey = Deno.env.get("ZULIP_BOT_API_KEY");
    if (!email || !apiKey) throw new Error("ZULIP_BOT_* env not set");
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

    const pushoverResp = await sendPushover({
        title: `🔔 Approval needed: Class ${r.action_class}`,
        message: `${r.target}\n${r.payload_summary}\n\nReact in Zulip: ${zulipHost}`,
        priority: 1,
        sound: "gamelan",
    });

    return { zulip_ok: zr.ok, pushover_ok: pushoverResp };
}

async function sendPushover(args: {
    title: string;
    message: string;
    priority: number;
    sound?: string;
}): Promise<boolean> {
    const token = Deno.env.get("PUSHOVER_APP_TOKEN");
    const user = Deno.env.get("PUSHOVER_USER_KEY");
    if (!token || !user) throw new Error("PUSHOVER_* env not set");
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
