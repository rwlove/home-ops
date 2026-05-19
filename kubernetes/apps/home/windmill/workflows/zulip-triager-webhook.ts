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

    // KNOWN ISSUE: langgraph-agents /inbox handler hangs after
    // receiving the request (same shape as the documented
    // /admin/tasks hang — see workflow_automation.md "Operating
    // notes"). Mitigation: fire-and-forget. We give the POST 2s,
    // which is enough for the body to land on the server side;
    // uvicorn keeps processing after we abort the client connection.
    // The langgraph specialist's eventual reply will land in this
    // same DM thread via the triager-bot's API key.
    const dispatchPayload = JSON.stringify({
        task_id,
        source: "zulip",
        content: text,
        user: "rob",
        // langgraph-agents 0.2.11+ uses this to DM the final graph
        // output back to the originating user from triager-bot.
        // Older versions silently ignore the extra field.
        zulip_user_id: message.sender_id,
    });
    let dispatched = false;
    try {
        await fetch(
            "http://langgraph-agents.ai.svc.cluster.local:8765/inbox",
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: dispatchPayload,
                signal: AbortSignal.timeout(2_000),
            },
        );
        dispatched = true;
    } catch (e) {
        // TimeoutError is expected — the handler hangs but the
        // request landed. Anything else (DNS / connect / etc.) is
        // a real failure.
        const msg = (e as Error).message ?? "";
        if (msg.includes("Signal timed out") || msg.includes("aborted")) {
            dispatched = true;
        } else {
            return {
                response_string: `⚠️ failed to dispatch · ${msg}`,
            };
        }
    }

    // Zulip expects {response_string} for the bot's acknowledgement
    // message in the same conversation. Keep it short — the agent's
    // real reply will arrive via langgraph-agents posting as the
    // triager-bot identity.
    const preview = text.length > 60 ? text.slice(0, 57) + "…" : text;
    return {
        response_string: dispatched
            ? `📥 dispatched · task \`${task_id}\` · _(processing async — reply will appear here when ready)_`
            : `⚠️ task \`${task_id}\` — failed to dispatch`,
    };
}
