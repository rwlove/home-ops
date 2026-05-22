// Webhook entry from langgraph-agents when a task pauses for approval.
//
// Emits two parallel surfaces:
//   1. ntfy → #approvals — phone push with tap-to-approve/reject/defer
//      action buttons. Each button POSTs a *pre-signed* HMAC token
//      directly to https://langgraph.${SECRET_DOMAIN}/approval, which
//      is path-restricted to the /approval endpoint only.
//   2. Zulip — DM to Robert (user 8) carrying the human-readable
//      approval card with magic-link buttons. Replaces the previous
//      stream-post-to-#approvals: stream posts required a subscription
//      Rob didn't have, and the in-cluster service URL was rejected
//      by Zulip's ALLOWED_HOSTS without an explicit Host header.
//
// `content` (the original user prompt) is carried in the inbound
// payload so the card can show "You asked: ..." — closes the gap
// where a bare ULID had no human-mappable context. langgraph-agents
// 0.2.39+ populates the field; older versions degrade gracefully.

import { crypto } from "https://deno.land/std@0.224.0/crypto/mod.ts";

type ApprovalRequest = {
    proposed_by: string;
    action_class: string;
    target: string;
    payload_summary: string;
    undo_path?: string;
    cost_estimate_usd?: number;
};

// Body keys from langgraph-agents: task_id + paused_for + content (0.2.39+).
export async function main(
    task_id: string,
    paused_for: { approval_request: ApprovalRequest },
    content?: string,
) {
    const r = paused_for.approval_request;

    // Pre-sign three tokens (one per verdict). The /approval endpoint
    // verifies via HMAC-SHA256 + checks the task is paused — only one
    // verdict can succeed per task.
    const [serverName, ...rest] = r.target.split(".");
    const method = rest.join(".");
    const approveTok = await signApprovalToken(task_id, r.action_class, serverName, method);
    const rejectTok = await signApprovalToken(task_id, r.action_class, serverName, method);
    const deferTok = await signApprovalToken(task_id, r.action_class, serverName, method);

    // Phone push — ntfy keeps its mobile-button UX. Title now uses
    // the human-readable class label instead of bare "Class C".
    const classLabel = ACTION_CLASS_LABEL[r.action_class] ?? `Class ${r.action_class}`;
    const agentLabel = AGENT_LABEL[r.proposed_by] ?? r.proposed_by;
    const ntfyResp = await publishNtfy({
        topic: "approvals",
        title: `${ACTION_CLASS_EMOJI[r.action_class] ?? "🔔"} ${agentLabel} needs approval`,
        message: [
            content ? `You asked: ${truncate(content, 200)}` : `Task: ${task_id}`,
            `Proposed: ${humanizeTarget(r.target)}`,
            `Class: ${classLabel}`,
            `Undo: ${r.undo_path ?? "(none)"}`,
            `Cost: $${r.cost_estimate_usd ?? 0}`,
        ].join("\n"),
        priority: r.action_class === "D" ? 5 : 4,
        tags: r.action_class === "D" ? ["warning", "rotating_light"] : ["warning"],
        actions: buildApprovalActions(task_id, approveTok, rejectTok, deferTok),
    });

    // Desktop — DM to Rob with the rich card.
    const zulipResp = await postZulipApprovalDM(
        task_id,
        r,
        content,
        approveTok,
        rejectTok,
        deferTok,
    );

    return { task_id, ntfy: ntfyResp, zulip: zulipResp };
}

// ---------- Humanizers ----------

// Action class semantics in this fleet (see homelab_dod / state.py):
//   A — observation / read-only (auto-approved)
//   B — single-side write, fully reversible
//   C — multi-side or reviewable change
//   D — destructive / irreversible (always approval-gated)
const ACTION_CLASS_EMOJI: Record<string, string> = {
    A: "🟢",
    B: "🟢",
    C: "🟡",
    D: "🔴",
};

const ACTION_CLASS_LABEL: Record<string, string> = {
    A: "🟢 A — routine",
    B: "🟢 B — reversible write",
    C: "🟡 C — reviewable change",
    D: "🔴 D — destructive / irreversible",
};

// Translate agent IDs into operator-facing role names. Keep keys aligned
// with src/agents/state.py ALL_AGENT_IDS in langgraph-agents.
const AGENT_LABEL: Record<string, string> = {
    "triager": "Triager",
    "supervisor": "Supervisor",
    "reviewer": "Reviewer",
    "reporter": "Reporter",
    "researcher": "Researcher",
    "note-maker": "Note maker",
    "coder": "Coder",
    "errand-runner": "Errand runner",
    "homelab-engineer": "Homelab agent",
    "network-operator": "Network agent",
    "storage-operator": "Storage agent",
    "smart-home-operator": "Smart-home agent",
    "ml-operator": "ML agent",
    "observability-operator": "Observability agent",
};

// Translate MCP tool targets like "kubectl-mcp.kubectl_apply" into a
// short verb phrase. Falls back to the bare target when unknown.
function humanizeTarget(target: string): string {
    const map: Record<string, string> = {
        "kubectl-mcp.kubectl_apply": "apply a kubectl manifest",
        "kubectl-mcp.kubectl_delete": "delete a Kubernetes resource",
        "kubectl-mcp.kubectl_scale": "scale a workload",
        "kubectl-mcp.kubectl_restart_deployment": "restart a deployment",
        "ha-mcp.call_service": "call a Home Assistant service",
        "ha-mcp.ha_call_service": "call a Home Assistant service",
        "ha-mcp.ha_set_entity": "change a Home Assistant entity",
        "ha-mcp.ha_set_helper": "change a Home Assistant helper",
        "omada-mcp.applyConfig": "apply an Omada network change",
    };
    if (map[target]) return map[target];
    // Generic fallback — split server/method, e.g. "foo-mcp.bar_baz"
    // → "use foo-mcp to bar baz".
    const dot = target.indexOf(".");
    if (dot > 0) {
        const server = target.slice(0, dot);
        const method = target.slice(dot + 1).replace(/_/g, " ");
        return `use ${server} to ${method}`;
    }
    return target;
}

function truncate(s: string, n: number): string {
    if (!s) return "";
    const oneLine = s.replace(/\r?\n+/g, " ").trim();
    return oneLine.length > n ? oneLine.slice(0, n - 1) + "…" : oneLine;
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

// ---------- Zulip DM to Rob ----------

async function postZulipApprovalDM(
    task_id: string,
    r: ApprovalRequest,
    content: string | undefined,
    approveTok: string,
    rejectTok: string,
    deferTok: string,
) {
    const agentLabel = AGENT_LABEL[r.proposed_by] ?? r.proposed_by;
    const classLabel = ACTION_CLASS_LABEL[r.action_class] ?? `Class ${r.action_class}`;
    const approvalUrl = `https://langgraph.${Deno.env.get("SECRET_DOMAIN") ?? "thesteamedcrab.com"}/approval`;
    // Magic-link bodies — pre-signed HMAC tokens. /approval endpoint
    // accepts both POST-from-ntfy and GET-by-link (the langgraph-agents
    // side handles the GET variant; HMAC binding makes link leaks
    // single-use). See errand_runner._verify_approval_token.
    const approveLink = `${approvalUrl}?task_id=${encodeURIComponent(task_id)}&reaction=approve&approval_token=${encodeURIComponent(approveTok)}&actor=rob`;
    const rejectLink = `${approvalUrl}?task_id=${encodeURIComponent(task_id)}&reaction=reject&approval_token=${encodeURIComponent(rejectTok)}&actor=rob`;
    const deferLink = `${approvalUrl}?task_id=${encodeURIComponent(task_id)}&reaction=defer&approval_token=${encodeURIComponent(deferTok)}&actor=rob`;

    const lines: string[] = [];
    if (content) {
        lines.push(`**You asked:** ${truncate(content, 200)}`);
        lines.push("");
    }
    lines.push(`${classLabel}`);
    lines.push("");
    lines.push(`**${agentLabel}** wants to **${humanizeTarget(r.target)}**:`);
    lines.push("");
    lines.push("```quote");
    lines.push(truncate(r.payload_summary, 600));
    lines.push("```");
    lines.push("");
    lines.push(
        `**Cost:** $${r.cost_estimate_usd ?? 0}  •  **Undo:** ${r.undo_path ?? "_not available_"}`,
    );
    lines.push("");
    lines.push(
        `→ [Approve](${approveLink})  •  [Reject](${rejectLink})  •  [Defer 4h](${deferLink})`,
    );
    lines.push("");
    lines.push(`_Or reply to this DM:_ \`approve ${task_id}\` / \`reject ${task_id}\` / \`defer ${task_id}\``);

    const content_md = lines.join("\n");

    const email = Deno.env.get("ZULIP_BOT_EMAIL");
    const apiKey = Deno.env.get("ZULIP_BOT_API_KEY");
    if (!email || !apiKey) throw new Error("ZULIP_BOT_EMAIL / ZULIP_BOT_API_KEY env not set");
    const robId = parseInt(Deno.env.get("ROB_ZULIP_USER_ID") ?? "8", 10);
    return await postZulip({ to: `[${robId}]`, type: "private", content: content_md, email, apiKey });
}

// Shared Zulip POST. Sets `Host: chat.<domain>` explicitly because the
// in-cluster service URL isn't in Zulip's ALLOWED_HOSTS — without this
// every request gets a 400 before the API handler sees it (same bug
// class as langgraph-agents PR #64). This bit the previous version
// silently: postZulipApprovalCard logged `"zulip": {}` for two months.
export async function postZulip(args: {
    to: string;
    type: "private" | "stream";
    topic?: string;
    content: string;
    email: string;
    apiKey: string;
}) {
    const auth = "Basic " + btoa(`${args.email}:${args.apiKey}`);
    const zulipApiUrl = Deno.env.get("ZULIP_API_URL") ?? "http://zulip.collab.svc.cluster.local";
    const zulipHostHeader = Deno.env.get("ZULIP_HOST_HEADER") ?? `chat.${Deno.env.get("SECRET_DOMAIN") ?? "thesteamedcrab.com"}`;
    const params: Record<string, string> = { type: args.type, to: args.to, content: args.content };
    if (args.topic) params.topic = args.topic;
    const form = new URLSearchParams(params);
    const zr = await fetch(`${zulipApiUrl}/api/v1/messages`, {
        method: "POST",
        headers: {
            Authorization: auth,
            "Content-Type": "application/x-www-form-urlencoded",
            Host: zulipHostHeader,
        },
        body: form,
        signal: AbortSignal.timeout(30_000),
    });
    const zResult = await zr.json().catch(() => ({}));
    return { status: zr.status, ok: zr.ok, ...zResult };
}
