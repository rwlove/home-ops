// Cron: weekly, Sunday 07:00 America/New_York.
//
// Fires the `storage-operator` agent on a weekly storage health
// sweep. Class A only — storage-operator drafts findings; any
// Class C remediation it proposes would route through errand-runner
// with the eight-clause execution gate. This cron's job is the
// surveillance, not the action.
//
// What the sweep checks:
//   - PV / PVC fill rates across ceph-block, Longhorn, NFS-backed
//   - Longhorn recurring-job success (daily snapshot, weekly +
//     monthly backup labels) per the storage-class instructions
//   - CNPG Barman backup recency for the 25 Postgres clusters
//   - Ceph OSD health + PG state + capacity headroom
//   - Garage substrate (NFS-backed) PVC fill + S3 endpoint reachability
//   - NFS mount health (beast `mass_storage` RAID6, brain
//     `mass_storage` RAID6) — storage-class.instructions.md is the
//     authoritative durability matrix
//
// Pinned `target_agent: "storage-operator"` to bypass triager
// mis-routing (same pattern as historian + reviewer crons).
//
// Result lands in Zulip stream #digests, topic
// `storage-health-YYYY-Www`. Findings are passed through the reporter
// agent (universal final-hop user-facing messenger) so the body is
// already markdown-shaped for Zulip.

type InboxResp = { task_id?: string; status?: string };
type AdminTaskResp = {
    task_id?: string;
    queue?: {
        status?: string;
        result?: { output?: string } | null;
        last_error?: string | null;
    };
    checkpointer?: { values?: { output?: string } };
};

const LG_BASE = "http://langgraph-agents.ai.svc.cluster.local:8765";
const POLL_INTERVAL_MS = 5_000;
const POLL_TIMEOUT_MS = 900_000; // 15 min — storage-operator may walk many
                                  // resources (25 CNPG clusters, Ceph state,
                                  // Longhorn volumes); give it room

function lgaHeaders(): Record<string, string> {
    const tok = Deno.env.get("HAI_CLI_TOKEN");
    return tok ? { Authorization: `Bearer ${tok}` } : {};
}

// ISO week — same helper shape as langgraph-reviewer-weekly.ts. Kept
// inlined rather than shared so each workflow stays self-contained
// (Windmill ts files don't import from each other).
function isoWeek(d: Date): { year: number; week: number } {
    const utc = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
    const dayNum = utc.getUTCDay() || 7;
    utc.setUTCDate(utc.getUTCDate() + 4 - dayNum);
    const yearStart = new Date(Date.UTC(utc.getUTCFullYear(), 0, 1));
    const weekNum = Math.ceil(
        (((utc.getTime() - yearStart.getTime()) / 86400000) + 1) / 7,
    );
    return { year: utc.getUTCFullYear(), week: weekNum };
}

export async function main() {
    const now = new Date();
    const date = now.toISOString().slice(0, 10);
    const { year, week } = isoWeek(now);
    const weekStr = `${year}-W${String(week).padStart(2, "0")}`;
    const client_task_id = `storage-health-${weekStr}`;

    // Storage-operator's persona expects a clear scope statement plus
    // an explicit "Class A only" constraint to keep the sweep from
    // overreaching into proposed remediations the operator hasn't
    // signed off on. The agent's eight-clause execution gate would
    // catch most overreach anyway, but a clear scope hint reduces
    // wasted reasoning on bigger gate evaluations.
    const promptBody = [
        "WEEKLY STORAGE HEALTH SWEEP — Class A analysis only.",
        "",
        "Walk the cluster storage hierarchy and report findings.",
        "Anything you'd recommend changing, list as Class A findings",
        "with file paths and rationale — do NOT escalate to Class C/D",
        "in this run (a follow-up task can be filed for any single",
        "finding the operator chooses to act on).",
        "",
        "Coverage:",
        "  1. PV / PVC fill: any volume >80% used? List with %.",
        "  2. Longhorn: recurring weekly-backups + monthly-backups",
        "     have they actually run in the last cadence window?",
        "     Any failed jobs in the last 7d?",
        "  3. CNPG Barman: last successful backup per cluster",
        "     (postgres-*). Any cluster missing a backup in 48h+?",
        "  4. Ceph: cluster status, OSD up/in count, near-full warns,",
        "     pg states (active+clean vs degraded/remapped).",
        "  5. Garage: substrate PVC fill (garage-data, garage-meta),",
        "     S3 endpoint reachable.",
        "  6. NFS: mounts on consumer pods responding (no stale",
        "     handles, no permission-denied loops).",
        "",
        "Output: ranked findings (highest-risk first), each with",
        "evidence + a one-sentence \"why it matters\" + a single",
        "recommended next step (no auto-actions, just guidance).",
        "",
        "Reference: storage-class.instructions.md for the durability",
        "matrix per backend.",
    ].join("\n");

    const lgResp = await fetch(`${LG_BASE}/inbox`, {
        method: "POST",
        headers: { ...lgaHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({
            task_id: client_task_id,
            source: "scheduled",
            target_agent: "storage-operator",
            content: promptBody,
            user: "rob",
        }),
        signal: AbortSignal.timeout(30_000),
    });
    if (!lgResp.ok) {
        throw new Error(`/inbox enqueue failed: HTTP ${lgResp.status}`);
    }
    const lg: InboxResp = (await lgResp.json().catch(() => ({}))) as InboxResp;
    const queue_task_id = lg.task_id;
    if (!queue_task_id) {
        throw new Error(`/inbox response missing task_id: ${JSON.stringify(lg)}`);
    }

    const output = await pollForOutput(queue_task_id);

    const content = output ||
        `Weekly storage health sweep complete; storage-operator wrote ` +
            `findings to vault. (No prose summary returned; check ` +
            `vault/reports/storage-health-${weekStr}.md.)`;

    // Post to Zulip stream #digests, topic storage-health-YYYY-Www.
    const email = Deno.env.get("ZULIP_BOT_EMAIL");
    const apiKey = Deno.env.get("ZULIP_BOT_API_KEY");
    if (!email || !apiKey) throw new Error("ZULIP_BOT_EMAIL / ZULIP_BOT_API_KEY env not set");
    const auth = "Basic " + btoa(`${email}:${apiKey}`);
    const zulipApiUrl = Deno.env.get("ZULIP_API_URL") ?? "http://zulip.collab.svc.cluster.local";
    const form = new URLSearchParams({
        type: "stream",
        to: "digests",
        topic: `storage-health-${weekStr}`,
        content,
    });
    const zr = await fetch(`${zulipApiUrl}/api/v1/messages`, {
        method: "POST",
        headers: {
            Authorization: auth,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        body: form,
        signal: AbortSignal.timeout(30_000),
    });
    const zResult = await zr.json().catch(() => ({}));

    return {
        client_task_id,
        queue_task_id,
        week: weekStr,
        date,
        zulip: zResult.result ?? zResult,
    };
}

async function pollForOutput(taskId: string): Promise<string | null> {
    const deadline = Date.now() + POLL_TIMEOUT_MS;
    while (Date.now() < deadline) {
        const r = await fetch(
            `${LG_BASE}/admin/tasks/${encodeURIComponent(taskId)}`,
            { signal: AbortSignal.timeout(15_000), headers: lgaHeaders() },
        );
        if (!r.ok) {
            if (r.status === 404) {
                await sleep(POLL_INTERVAL_MS);
                continue;
            }
            throw new Error(`/admin/tasks/${taskId} returned HTTP ${r.status}`);
        }
        const body = (await r.json().catch(() => ({}))) as AdminTaskResp;
        const status = body.queue?.status;
        if (status === "done") {
            const queued_output = body.queue?.result?.output;
            const cp_output = body.checkpointer?.values?.output;
            return queued_output ?? cp_output ?? null;
        }
        if (body.queue?.last_error) {
            throw new Error(`task ${taskId} failed: ${body.queue.last_error}`);
        }
        await sleep(POLL_INTERVAL_MS);
    }
    throw new Error(`poll timeout waiting for task ${taskId} (${POLL_TIMEOUT_MS}ms)`);
}

function sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
}
