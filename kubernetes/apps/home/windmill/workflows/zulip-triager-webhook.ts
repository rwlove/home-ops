// Outgoing-webhook target for the Triager 📥 Zulip bot.
//
// Zulip routes any DM to triager-bot (or @-mention of it in a stream)
// to this script. We translate the Zulip event into a langgraph-agents
// /inbox POST and return a fast acknowledgement back to Zulip — the
// agent's actual reply is posted asynchronously by langgraph-agents
// using the triager-bot's own API key, landing in the same DM thread.
//
// We do NOT delegate to f/lovenet/langgraph-inbox: that script expects
// a {task_id, source, content, user} body shape, while Zulip outgoing
// webhooks send {data, trigger, message, bot_email}. Keeping the
// adapter separate also means voice/HA callers don't pay the
// Zulip-payload-parsing tax.

type ZulipOutgoingMessage = {
    sender_id: number;
    sender_email: string;
    sender_full_name: string;
    subject?: string;
    topic?: string;
    content: string;
    type?: "stream" | "private";
};

export async function main(
    data?: string,
    trigger?: string,
    message?: ZulipOutgoingMessage,
    bot_email?: string,
) {
    const robId = parseInt(Deno.env.get("ROB_ZULIP_USER_ID") ?? "0", 10);
    if (!message || message.sender_id !== robId) {
        return {
            response_not_required: true,
            skip: true,
            reason: "sender not Rob",
            sender_id: message?.sender_id,
        };
    }

    const text = (data ?? message.content ?? "").trim();
    if (!text) {
        return {
            response_string: "⚠️ empty message — ignored",
            response_not_required: false,
        };
    }

    const task_id = `zulip-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

    // langgraph /inbox is normally fast for the initial dispatch — it
    // returns once the task is queued + (maybe) the triager classified
    // the intent. The actual specialist work runs async after the
    // POST returns. 5s timeout keeps us under Zulip's 10s webhook
    // deadline.
    let status = "queued";
    try {
        const r = await fetch(
            "http://langgraph-agents.ai.svc.cluster.local:8765/inbox",
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    task_id,
                    source: "zulip",
                    content: text,
                    user: "rob",
                }),
                signal: AbortSignal.timeout(5_000),
            },
        );
        const lg = await r.json().catch(() => ({}));
        status = lg.status ?? "ok";
    } catch (e) {
        status = `error: ${(e as Error).message}`;
    }

    // Zulip expects {response_string} for the bot's acknowledgement
    // message in the same conversation. Keep it short — the agent's
    // real reply will arrive via langgraph-agents posting as the
    // triager-bot identity.
    const preview = text.length > 60 ? text.slice(0, 57) + "…" : text;
    return {
        response_string: `📥 received · task \`${task_id}\` · status: ${status}\n_(preview: ${preview})_`,
    };
}
