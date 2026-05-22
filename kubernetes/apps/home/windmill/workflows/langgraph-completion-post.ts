// Webhook entry from langgraph-agents when a task completes
// (status → done in the queue, no pending interrupt).
//
// Emits a Zulip DM to Rob with a human-readable completion card:
//   - which agent ran it (target_agent)
//   - the original prompt (content)
//   - the output, truncated
//   - duration
//   - link to `hai task show <id>` for the full output
//
// Companion to langgraph-approval-post: that handler covers paused
// tasks; this one covers terminal-success. Failure/dlq is handled
// by langgraph-dlq-watcher and doesn't fan out here.
//
// Sets explicit Host header on the Zulip POST — the in-cluster
// service URL isn't in Zulip's ALLOWED_HOSTS (same bug as PR #64).

type CompletionPayload = {
    task_id: string;
    target_agent?: string;
    content?: string;
    output?: string;
    duration_s?: number;
};

export async function main(
    task_id: string,
    target_agent?: string,
    content?: string,
    output?: string,
    duration_s?: number,
) {
    const agentLabel = AGENT_LABEL[target_agent ?? ""] ?? target_agent ?? "Agent";
    const haiUrl = `https://hai.${Deno.env.get("SECRET_DOMAIN") ?? "thesteamedcrab.com"}/admin/tasks/${encodeURIComponent(task_id)}`;

    const lines: string[] = [];
    if (content) {
        lines.push(`**You asked:** ${truncate(content, 200)}`);
        lines.push("");
    }
    lines.push(`✅ **${agentLabel}** finished${duration_s ? ` in ${formatDuration(duration_s)}` : ""}.`);
    if (output) {
        lines.push("");
        lines.push("```quote");
        lines.push(truncate(output, 800));
        lines.push("```");
    }
    lines.push("");
    lines.push(`_Full output:_ \`hai task show ${task_id}\` or [open in API](${haiUrl})`);

    const content_md = lines.join("\n");

    const email = Deno.env.get("ZULIP_BOT_EMAIL");
    const apiKey = Deno.env.get("ZULIP_BOT_API_KEY");
    if (!email || !apiKey) throw new Error("ZULIP_BOT_EMAIL / ZULIP_BOT_API_KEY env not set");
    const robId = parseInt(Deno.env.get("ROB_ZULIP_USER_ID") ?? "8", 10);

    const zulipResp = await postZulip({
        to: `[${robId}]`,
        type: "private",
        content: content_md,
        email,
        apiKey,
    });

    return { task_id, target_agent, zulip: zulipResp };
}

// ---------- Humanizers ----------

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

function truncate(s: string, n: number): string {
    if (!s) return "";
    const oneLine = s.replace(/\r?\n+/g, "\n").trim();
    return oneLine.length > n ? oneLine.slice(0, n - 1) + "…" : oneLine;
}

function formatDuration(seconds: number): string {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds % 60);
    return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

// ---------- Zulip ----------

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
    const zulipHostHeader = Deno.env.get("ZULIP_HOST_HEADER") ??
        `chat.${Deno.env.get("SECRET_DOMAIN") ?? "thesteamedcrab.com"}`;
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
