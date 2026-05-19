// Outgoing-webhook target for the @Approval-Receiver Zulip bot.
//
// Zulip outgoing webhooks fire on messages (not reactions), so the
// approval UX is: Rob types "@Approval-Receiver approve" (or reject /
// defer) inside the topic for the approval request. We:
//
//   1. Verify the sender is Rob (sender_id match against
//      ROB_ZULIP_USER_ID)
//   2. Parse the message body for an intent keyword
//   3. Parse the topic for task_id + class + target (same format as
//      approval-post used: "<task_id> — Class <C/D>: <action>")
//   4. HMAC-sign an approval token
//   5. POST it to langgraph-agents /approval
//
// Bot ingress uses ?token=<TOKEN> in the URL (Zulip outgoing webhook
// posts can't carry custom Authorization headers).

import { crypto } from "https://deno.land/std@0.224.0/crypto/mod.ts";

type ZulipOutgoingMessage = {
    sender_id: number;
    sender_email: string;
    subject: string; // alias for topic
    topic?: string;
    content: string;
    stream_id?: number;
    type?: "stream" | "private";
};

// Zulip outgoing-webhook payload top-level keys.
// `data` is the message text with the bot mention stripped.
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
    if (message.type !== "stream") {
        return {
            response_not_required: true,
            skip: true,
            reason: "not a stream message",
            type: message.type,
        };
    }

    // Parse intent from message text (already has the @mention stripped by Zulip).
    const text = (data ?? "").toLowerCase();
    let reaction: "approve" | "reject" | "defer" | undefined;
    if (/\bapprove\b/.test(text) || text.includes("👍") || text.includes(":thumbs_up:")) {
        reaction = "approve";
    } else if (/\breject\b/.test(text) || text.includes("👎") || text.includes(":thumbs_down:")) {
        reaction = "reject";
    } else if (/\bdefer\b/.test(text) || text.includes("⏸️") || text.includes(":pause:")) {
        reaction = "defer";
    } else {
        return {
            response_not_required: true,
            skip: true,
            reason: "no approve/reject/defer keyword in message",
            text: text.slice(0, 80),
        };
    }

    // Topic format: "<task_id> — Class <C/D>: <action>"
    const topic = message.topic ?? message.subject ?? "";
    const taskMatch = topic.match(/^([\w-]+)\s/);
    if (!taskMatch) {
        return { response_not_required: true, skip: true, reason: "no task_id in topic", topic };
    }
    const task_id = taskMatch[1];

    const classMatch = topic.match(/Class\s+([ABCD]):/);
    const targetMatch = topic.match(/Class\s+[ABCD]:\s*(\S+)/);
    if (!classMatch || !targetMatch) {
        return { response_not_required: true, skip: true, reason: "malformed topic", topic };
    }
    const action_class = classMatch[1];
    const target = targetMatch[1];
    const [server, ...rest] = target.split(".");
    const method = rest.join(".");

    const secret = Deno.env.get("LANGGRAPH_APPROVAL_SIGNING_KEY");
    if (!secret) throw new Error("LANGGRAPH_APPROVAL_SIGNING_KEY env not set");

    const nonceBytes = new Uint8Array(8);
    crypto.getRandomValues(nonceBytes);
    const nonce = Array.from(nonceBytes).map((b) => b.toString(16).padStart(2, "0")).join("");
    const payload = `${task_id}|${action_class}|${server}|${method}|${nonce}`;
    const sig = await hmacSha256Hex(secret, payload);
    const approval_token = `${payload}:${sig}`;

    // langgraph /approval is normally fast (<1s) — validate token,
    // resume the paused task, return. 5s timeout to stay safely under
    // Zulip's ~10s webhook deadline (with headroom for HMAC sig).
    //
    // We intentionally don't post a Zulip ack from this script:
    // Rob's @-mention in #approvals is the user-visible confirmation
    // that the bot was triggered. langgraph-agents posts the task's
    // own status update via its zulip integration on /approval
    // success.
    let lgStatus = "queued";
    try {
        const r = await fetch(
            "http://langgraph-agents.ai.svc.cluster.local:8765/approval",
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ task_id, reaction, approval_token, actor: "rob" }),
                signal: AbortSignal.timeout(5_000),
            },
        );
        const lgResult = await r.json().catch(() => ({}));
        lgStatus = lgResult.status ?? "ok";
    } catch (e) {
        lgStatus = `error: ${(e as Error).message}`;
    }

    return {
        response_not_required: true,
        status: lgStatus,
        task_id,
        reaction,
    };
}

async function postAck(topic: string, reaction: string, lgStatus: string) {
    const email = Deno.env.get("ZULIP_BOT_EMAIL");
    const apiKey = Deno.env.get("ZULIP_BOT_API_KEY");
    if (!email || !apiKey) return;
    const auth = "Basic " + btoa(`${email}:${apiKey}`);
    const zulipHost = Deno.env.get("ZULIP_HOST") ?? "chat.thesteamedcrab.com";
    const emoji = reaction === "approve" ? "✅" : reaction === "reject" ? "🛑" : "⏸";
    const form = new URLSearchParams({
        type: "stream",
        to: "approvals",
        topic,
        content: `${emoji} **${reaction}** → langgraph status: \`${lgStatus ?? "ok"}\``,
    });
    await fetch(`https://${zulipHost}/api/v1/messages`, {
        method: "POST",
        headers: {
            Authorization: auth,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        body: form,
        signal: AbortSignal.timeout(15_000),
    });
}

async function hmacSha256Hex(secret: string, msg: string): Promise<string> {
    const enc = new TextEncoder();
    const key = await crypto.subtle.importKey(
        "raw",
        enc.encode(secret),
        { name: "HMAC", hash: "SHA-256" },
        false,
        ["sign"],
    );
    const sig = await crypto.subtle.sign("HMAC", key, enc.encode(msg));
    return Array.from(new Uint8Array(sig)).map((b) => b.toString(16).padStart(2, "0")).join("");
}
