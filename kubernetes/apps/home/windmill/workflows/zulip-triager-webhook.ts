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

    // Approval-verb fallback path. The approval DM card includes
    //   `_Or reply to this DM:_ approve <task_id> / reject / defer`
    // and this branch handles those replies in the same DM channel
    // the card was sent in. Magic-link buttons in the card cover the
    // primary path; this is the no-link / paste-from-history fallback.
    const verb = parseApprovalVerb(text);
    if (verb) {
        const dispatch = await dispatchApprovalVerb(verb);
        return { response_string: dispatch };
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

// ---------- Approval verb dispatch ----------

type ApprovalVerb = { reaction: "approve" | "reject" | "defer"; task_id: string };

// Recognize  `approve <task_id>` / `reject <task_id>` / `defer <task_id>`
// at the start of the message, possibly preceded by an @-mention
// (e.g. "@**triager-bot** approve 01KS...").
function parseApprovalVerb(text: string): ApprovalVerb | null {
    const cleaned = text.replace(/^\s*@\*\*[^*]+\*\*\s*/, "").trim();
    const m = cleaned.match(/^(approve|reject|defer)\s+([A-Za-z0-9_-]+)\s*$/i);
    if (!m) return null;
    return { reaction: m[1].toLowerCase() as ApprovalVerb["reaction"], task_id: m[2] };
}

async function dispatchApprovalVerb(verb: ApprovalVerb): Promise<string> {
    // Round-trip via existing endpoints — no new langgraph-agents API:
    //   1. GET /admin/tasks/<id> to read the pending interrupt's
    //      action_class + target (we need them to bind the HMAC).
    //   2. Sign an HMAC token with LANGGRAPH_APPROVAL_SIGNING_KEY,
    //      identical to what langgraph-approval-post does for the
    //      magic links + ntfy buttons.
    //   3. POST to /approval with the standard body shape.
    const lgaBase = "http://langgraph-agents.ai.svc.cluster.local:8765";
    const haiToken = Deno.env.get("HAI_CLI_TOKEN");
    if (!haiToken) {
        return `⚠️ HAI_CLI_TOKEN env not set on this workflow. Use the [Approve] link in the original card instead.`;
    }

    let interrupt: { action_class?: string; target?: string } | null = null;
    try {
        const r = await fetch(`${lgaBase}/admin/tasks/${encodeURIComponent(verb.task_id)}`, {
            headers: { Authorization: `Bearer ${haiToken}` },
            signal: AbortSignal.timeout(10_000),
        });
        if (!r.ok) {
            return `⚠️ task \`${verb.task_id}\` lookup returned ${r.status}`;
        }
        const j = await r.json();
        const interrupts = j?.checkpointer?.interrupts ?? [];
        for (const i of interrupts) {
            if (i?.value?.action_class) {
                interrupt = i.value;
                break;
            }
        }
    } catch (e) {
        return `⚠️ task lookup failed · ${(e as Error).message ?? "unknown"}`;
    }
    if (!interrupt || !interrupt.target || !interrupt.action_class) {
        return `⚠️ task \`${verb.task_id}\` has no pending approval interrupt`;
    }

    const [serverName, ...rest] = interrupt.target.split(".");
    const method = rest.join(".");
    const token = await signApprovalToken(
        verb.task_id,
        interrupt.action_class,
        serverName,
        method,
    );

    const approvalUrl = `https://langgraph.${Deno.env.get("SECRET_DOMAIN") ?? "thesteamedcrab.com"}/approval`;
    try {
        const r = await fetch(approvalUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                task_id: verb.task_id,
                reaction: verb.reaction,
                approval_token: token,
                actor: "rob",
            }),
            signal: AbortSignal.timeout(10_000),
        });
        if (r.ok) {
            return `✅ \`${verb.reaction}\` dispatched for task \`${verb.task_id}\``;
        }
        const body = await r.text().catch(() => "");
        return `⚠️ /approval returned ${r.status} · ${body.slice(0, 200)}`;
    } catch (e) {
        return `⚠️ /approval call failed · ${(e as Error).message ?? "unknown"}`;
    }
}

// HMAC-SHA256 token signing — matches langgraph-approval-post.ts
// and the langgraph-agents errand_runner._verify_approval_token.
async function signApprovalToken(
    task_id: string,
    action_class: string,
    server: string,
    method: string,
): Promise<string> {
    const secret = Deno.env.get("LANGGRAPH_APPROVAL_SIGNING_KEY");
    if (!secret) throw new Error("LANGGRAPH_APPROVAL_SIGNING_KEY env not set");
    const nonceBytes = new Uint8Array(8);
    crypto.getRandomValues(nonceBytes);
    const nonce = Array.from(nonceBytes).map((b) => b.toString(16).padStart(2, "0")).join("");
    const payload = `${task_id}|${action_class}|${server}|${method}|${nonce}`;
    const enc = new TextEncoder();
    const key = await crypto.subtle.importKey(
        "raw",
        enc.encode(secret),
        { name: "HMAC", hash: "SHA-256" },
        false,
        ["sign"],
    );
    const sig = await crypto.subtle.sign("HMAC", key, enc.encode(payload));
    const sigHex = Array.from(new Uint8Array(sig))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
    return `${payload}:${sigHex}`;
}
