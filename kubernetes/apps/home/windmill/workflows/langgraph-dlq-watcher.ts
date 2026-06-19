// Cron: every 5 minutes.
//
// Phase 4.M3 — DLQ surface. Polls langgraph-agents `/admin/dlq` and
// posts new entries to Zulip `operations` topic with action buttons
// (ack / inspect). Tracks last-seen ULID via Windmill state to avoid
// re-posting.
//
// Companion to lga 4.M3 (`/admin/dlq` endpoints + Phase 4.M2 worker
// that writes to task_dlq on terminal failure).

// `deno.land/x/wmill` was retired in favor of the npm package; the
// deno.land/x path now returns 404, so any script using it fails at
// module-resolution time. Pin the same version via `npm:` instead.
import * as wmill from "npm:windmill-client@1.470.0";

type DlqEntry = {
    id: string;
    envelope: Record<string, unknown>;
    attempts: number;
    last_error: string | null;
    dlq_at: string | null;
};

const LG_BASE = "http://langgraph-agents.ai.svc.cluster.local:8765";
const STATE_KEY = "f/lovenet/langgraph_dlq_last_seen_id";

// HAI_CLI_TOKEN gates /admin/* + /inbox on langgraph-agents since 0.2.38
// (PR-C bearer-auth middleware). Inject the token into every call to the
// in-cluster langgraph-agents Service URL. Returns an empty header dict
// when the env is unset (dev / pre-deployment) — server-side falls back
// to allow-all in that mode, so the workflow stays functional.
function lgaHeaders(): Record<string, string> {
    const tok = Deno.env.get("HAI_CLI_TOKEN");
    return tok ? { Authorization: `Bearer ${tok}` } : {};
}

export async function main() {
    const lastSeen = await getLastSeen();

    const url = lastSeen
        ? `${LG_BASE}/admin/dlq?since_id=${encodeURIComponent(lastSeen)}&limit=20`
        : `${LG_BASE}/admin/dlq?limit=20`;
    const resp = await fetch(url, { signal: AbortSignal.timeout(15_000), headers: lgaHeaders() });
    if (!resp.ok) {
        return { skip: true, reason: `GET /admin/dlq returned HTTP ${resp.status}` };
    }
    const entries = (await resp.json().catch(() => [])) as DlqEntry[];
    if (!Array.isArray(entries) || entries.length === 0) {
        return { posted: 0, last_seen: lastSeen };
    }

    // Entries arrive newest-first. We need to post oldest-first so the
    // operator reads them in chronological order; reverse before iterating.
    const ordered = [...entries].reverse();
    let postedCount = 0;
    let newestId = lastSeen ?? "";

    for (const e of ordered) {
        await postToZulip(e);
        postedCount += 1;
        if (e.id > newestId) newestId = e.id;
    }

    await setLastSeen(newestId);
    return { posted: postedCount, last_seen: newestId };
}

async function getLastSeen(): Promise<string | null> {
    try {
        const value = await wmill.getState();
        if (typeof value === "string" && value.length > 0) return value;
        if (value && typeof value === "object" && "last_id" in value) {
            return (value as { last_id: string }).last_id;
        }
        return null;
    } catch {
        return null;
    }
}

async function setLastSeen(id: string): Promise<void> {
    try {
        await wmill.setState({ last_id: id, updated_at: new Date().toISOString() });
    } catch {
        // Non-fatal — next run picks up newer entries via the SQL `since_id`
        // filter regardless.
    }
}

async function postToZulip(entry: DlqEntry): Promise<void> {
    const email = Deno.env.get("ZULIP_BOT_EMAIL");
    const apiKey = Deno.env.get("ZULIP_BOT_API_KEY");
    if (!email || !apiKey) {
        console.error("zulip creds not configured; skipping post");
        return;
    }
    const auth = "Basic " + btoa(`${email}:${apiKey}`);
    const zulipApiUrl = Deno.env.get("ZULIP_API_URL") ?? "http://zulip.collab.svc.cluster.local";

    const env = entry.envelope;
    const taskIdHint = (env.task_id as string) ?? entry.id;
    const source = (env.source as string) ?? "unknown";
    const user = (env.user as string) ?? "unknown";
    const errSnippet = (entry.last_error ?? "").slice(0, 1200);

    const content = [
        `🪦 **DLQ task** \`${entry.id}\``,
        `Client task_id: \`${taskIdHint}\``,
        `Source: \`${source}\` · User: \`${user}\` · Attempts: ${entry.attempts}`,
        `Failed at: ${entry.dlq_at ?? "(unknown)"}`,
        ``,
        `\`\`\``,
        errSnippet || "(no error message)",
        `\`\`\``,
        ``,
        `Ack and drop:`,
        `  \`curl -X DELETE ${LG_BASE}/admin/dlq/${entry.id}\``,
    ].join("\n");

    const form = new URLSearchParams({
        type: "stream",
        to: "operations",
        topic: `dlq-${(entry.dlq_at ?? "").slice(0, 10) || "unknown"}`,
        content,
    });
    await fetch(`${zulipApiUrl}/api/v1/messages`, {
        method: "POST",
        headers: {
            Authorization: auth,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        body: form,
        signal: AbortSignal.timeout(15_000),
    }).catch(() => {});
}
