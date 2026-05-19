// Triggered by Zulip outgoing-webhook on emoji reactions in #approvals.
//
// Verifies the reactor is Rob, maps the emoji → approve/reject/defer,
// parses task_id from the topic, signs an HMAC-SHA256 approval token,
// and POSTs it to langgraph-agents /approval.
//
// Replaces the n8n flow "LangGraph → Approval receive (Zulip reaction)".

import { crypto } from "https://deno.land/std@0.224.0/crypto/mod.ts";

type ZulipReactionEvent = {
    user_id?: number;
    emoji_name?: string;
    topic?: string;
    message?: { topic?: string };
};

export async function main(body: ZulipReactionEvent) {
    const robId = parseInt(Deno.env.get("ROB_ZULIP_USER_ID") ?? "0", 10);
    if (body.user_id !== robId) {
        return { skip: true, reason: "reactor is not Rob", user_id: body.user_id };
    }

    const emoji = body.emoji_name ?? "";
    let reaction: "approve" | "reject" | "defer" | undefined;
    if (emoji === "thumbs_up" || emoji === "+1") reaction = "approve";
    else if (emoji === "thumbs_down" || emoji === "-1") reaction = "reject";
    else if (emoji === "pause" || emoji === "pause_button") reaction = "defer";
    else return { skip: true, reason: "irrelevant emoji", emoji };

    // Topic format: "<task_id> — Class <C/D>: <action>"
    const topic = body.message?.topic ?? body.topic ?? "";
    const taskMatch = topic.match(/^([\w-]+)\s/);
    if (!taskMatch) return { skip: true, reason: "no task_id in topic", topic };
    const task_id = taskMatch[1];

    const classMatch = topic.match(/Class\s+([ABCD]):/);
    const targetMatch = topic.match(/Class\s+[ABCD]:\s*(\S+)/);
    if (!classMatch || !targetMatch) {
        return { skip: true, reason: "malformed topic", topic };
    }
    const action_class = classMatch[1];
    const target = targetMatch[1];
    const [server, ...rest] = target.split(".");
    const method = rest.join(".");

    // Sign approval token.
    const secret = Deno.env.get("LANGGRAPH_APPROVAL_SIGNING_KEY");
    if (!secret) throw new Error("LANGGRAPH_APPROVAL_SIGNING_KEY env not set");

    const nonceBytes = new Uint8Array(8);
    crypto.getRandomValues(nonceBytes);
    const nonce = Array.from(nonceBytes).map((b) => b.toString(16).padStart(2, "0")).join("");
    const payload = `${task_id}|${action_class}|${server}|${method}|${nonce}`;
    const sig = await hmacSha256Hex(secret, payload);
    const approval_token = `${payload}:${sig}`;

    // POST to langgraph-agents.
    const r = await fetch("http://langgraph-agents.ai.svc.cluster.local:8765/approval", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id, reaction, approval_token, actor: "rob" }),
        signal: AbortSignal.timeout(300_000),
    });
    const lgResult = await r.json().catch(() => ({}));
    return { status: lgResult.status ?? "completed", task_id, reaction };
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
