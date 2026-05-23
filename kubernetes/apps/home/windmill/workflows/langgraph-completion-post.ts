// Webhook entry from langgraph-agents when a task completes
// (status → done in the queue, no pending interrupt).
//
// Emits a Zulip DM to ADMIN with a completion card whose body is the
// `reporter` agent's rich-text rendering (lga v0.2.42+). Reporter is
// the universal final hop, so `output` is already conclusion-first
// markdown with clickable obsidian:// vault links + labeled URLs —
// this template appends only a compact meta footer.
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
    const haiUrl = `https://hai-web.${Deno.env.get("SECRET_DOMAIN") ?? "thesteamedcrab.com"}/admin/tasks/${encodeURIComponent(task_id)}`;
    const adminName = Deno.env.get("ADMIN_NAME") ?? "the admin";

    // Lead with the agent's CONCLUSION, not the verbose prompt.
    // The prior template buried the actual answer under a "You asked:"
    // wall of prompt text that the operator already wrote (or that the
    // workflow generated). Result: every DM looked the same.
    //
    // As of lga v0.2.42, the `reporter` agent is the universal final hop —
    // every chain's terminal `output` is reporter's own rich-text rendering
    // (bold-first-line conclusion + clickable obsidian:// + labeled URLs per
    // reporter's SOUL). So this template no longer applies the conclusion-
    // first heuristic; it emits reporter's output verbatim and just appends
    // the meta footer.
    //
    // Shape:
    //   <reporter's rich-text body, as-is>
    //   ---
    //   _meta: agent, duration, [optional one-line task hint], hai link_
    const lines: string[] = [];
    const out = (output ?? "").trim();
    if (out) {
        lines.push(out);
    } else {
        lines.push(`✅ **${agentLabel}** finished — no output.`);
    }

    // Compact meta footer — one line, clickable. Italic uses `*...*`
    // (Zulip's renderer treats `_..._` as literal underscores).
    //
    // Dropped from previous shape (visible-noise audit 2026-05-23):
    //   - "task: <truncated content>" — body already conveys the ask
    //   - "ADMIN — full output:" salutation — this IS a DM to ADMIN
    //   - bare `hai task show <ULID>` — duplicates the URL link target
    //   - separate "api" mini-link — promoted to the main "open task"
    //
    // Kept: agent label (debugging), duration (cost/latency signal),
    // one clickable URL (HTTPS so it works on every surface incl.
    // Gmail forwards).
    lines.push("");
    lines.push("---");
    const durStr = duration_s ? formatDuration(duration_s) : "done";
    lines.push(`*${agentLabel} · ${durStr} · [open task ↗](${haiUrl})*`);

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
    "historian": "Historian",
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
