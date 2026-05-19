// Webhook entry from langgraph-agents when a task pauses for approval.
//
// Emits two parallel surfaces:
//   1. ntfy → #approvals — phone push with tap-to-approve/reject/defer
//      action buttons. Each button POSTs a *pre-signed* HMAC token
//      directly to https://langgraph.${SECRET_DOMAIN}/approval, which
//      is path-restricted to the /approval endpoint only.
//   2. Zulip → #approvals — desktop fallback via emoji reaction
//      (handled by langgraph-approval-receive). Also serves as the
//      audit trail for the action class + payload summary.
//
// Replaces the n8n flow "LangGraph → Approval post to Zulip" and the
// prior Pushover-shape (PR #11672) that only carried a no-action push.

import { crypto } from "https://deno.land/std@0.224.0/crypto/mod.ts";

type ApprovalRequest = {
    proposed_by: string;
    action_class: string;
    target: string;
    payload_summary: string;
    undo_path?: string;
    cost_estimate_usd?: number;
};

// Body keys from langgraph-agents: task_id + paused_for.
export async function main(task_id: string, paused_for: { approval_request: ApprovalRequest }) {
    const r = paused_for.approval_request;
    const topic = `${task_id} — Class ${r.action_class}: ${r.target}`;

    // Pre-sign three tokens (one per verdict). The /approval endpoint
    // verifies via HMAC-SHA256 + checks the task is paused — only one
    // verdict can succeed per task.
    const [serverName, ...rest] = r.target.split(".");
    const method = rest.join(".");
    const approveTok = await signApprovalToken(task_id, r.action_class, serverName, method);
    const rejectTok = await signApprovalToken(task_id, r.action_class, serverName, method);
    const deferTok = await signApprovalToken(task_id, r.action_class, serverName, method);

    const ntfyResp = await publishNtfy({
        topic: "approvals",
        title: `🔔 Approval needed: Class ${r.action_class}`,
        message: [
            `Task: ${task_id}`,
            `Target: ${r.target}`,
            `Summary: ${r.payload_summary}`,
            `Undo: ${r.undo_path ?? "(none)"}`,
            `Cost: $${r.cost_estimate_usd ?? 0}`,
        ].join("\n"),
        priority: 5,
        tags: ["warning"],
        actions: buildApprovalActions(task_id, approveTok, rejectTok, deferTok),
        click: `https://chat.thesteamedcrab.com/#narrow/stream/approvals/topic/${encodeURIComponent(topic)}`,
    });

    const zulipResp = await postZulipApprovalCard(task_id, r);

    return { task_id, topic, ntfy: ntfyResp, zulip: zulipResp };
}

// ---------- ntfy ----------

type NtfyAction = {
    action: "http";
    label: string;
    url: string;
    method: "POST";
    headers: Record<string, string>;
    body: string;
    clear: true;
};

function buildApprovalActions(
    task_id: string,
    approveTok: string,
    rejectTok: string,
    deferTok: string,
): NtfyAction[] {
    const approvalUrl = `https://langgraph.${Deno.env.get("SECRET_DOMAIN") ?? "thesteamedcrab.com"}/approval`;
    const make = (label: string, reaction: string, token: string): NtfyAction => ({
        action: "http",
        label,
        url: approvalUrl,
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id, reaction, approval_token: token, actor: "rob" }),
        clear: true,
    });
    return [
        make("Approve", "approve", approveTok),
        make("Reject", "reject", rejectTok),
        make("Defer 4h", "defer", deferTok),
    ];
}

export async function publishNtfy(args: {
    topic: string;
    title: string;
    message: string;
    priority?: number;
    tags?: string[];
    actions?: NtfyAction[];
    click?: string;
}) {
    const url = Deno.env.get("NTFY_URL") ?? "https://ntfy.thesteamedcrab.com";
    const token = Deno.env.get("NTFY_WRITE_TOKEN");
    if (!token) throw new Error("NTFY_WRITE_TOKEN env not set");
    const body = {
        topic: args.topic,
        title: args.title,
        message: args.message,
        priority: args.priority ?? 3,
        tags: args.tags ?? [],
        ...(args.actions && args.actions.length > 0 ? { actions: args.actions } : {}),
        ...(args.click ? { click: args.click } : {}),
    };
    const r = await fetch(url, {
        method: "POST",
        headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(30_000),
    });
    return { status: r.status, ok: r.ok };
}

// ---------- HMAC token signing (mirrors langgraph-agents
//            errand_runner._verify_approval_token) ----------

export async function signApprovalToken(
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
    const sig = await hmacSha256Hex(secret, payload);
    return `${payload}:${sig}`;
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

// ---------- Zulip (audit + emoji fallback) ----------

async function postZulipApprovalCard(task_id: string, r: ApprovalRequest) {
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
        "Tap an action on the ntfy push, **or** @-mention from a desktop:",
        "  @**Approval Receiver** approve",
        "  @**Approval Receiver** reject",
        "  @**Approval Receiver** defer",
    ].join("\n");

    const email = Deno.env.get("ZULIP_BOT_EMAIL");
    const apiKey = Deno.env.get("ZULIP_BOT_API_KEY");
    if (!email || !apiKey) throw new Error("ZULIP_BOT_EMAIL / ZULIP_BOT_API_KEY env not set");
    const auth = "Basic " + btoa(`${email}:${apiKey}`);
    const zulipHost = Deno.env.get("ZULIP_HOST") ?? "chat.thesteamedcrab.com";
    const form = new URLSearchParams({ type: "stream", to: "approvals", topic, content });
    const zr = await fetch(`https://${zulipHost}/api/v1/messages`, {
        method: "POST",
        headers: { Authorization: auth, "Content-Type": "application/x-www-form-urlencoded" },
        body: form,
        signal: AbortSignal.timeout(30_000),
    });
    const zResult = await zr.json().catch(() => ({}));
    return zResult.result ?? zResult;
}
