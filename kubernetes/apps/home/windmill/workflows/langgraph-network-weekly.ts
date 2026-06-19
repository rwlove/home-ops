// Cron: weekly, Saturday 04:00 America/New_York (routine maintenance window).
//
// Fires the `network-operator` agent on a weekly Class-A network health
// sweep. Lovenet L1-L7 — Omada controller state vs netbox source-of-truth,
// VLAN consistency, AP/SSID config drift, BGP peering state, cert
// expiry windows, DNS zone integrity.
//
// Class A only — operator drafts findings; any Class C remediation
// (firewall rule, VLAN change, AP reassignment) routes through
// errand-runner with operator's prime-directive gate.
//
// Pinned `target_agent: "network-operator"` to bypass triager
// mis-routing across 20 alternatives.
//
// Result lands in Zulip stream #digests, topic
// `network-health-YYYY-Www`. Findings are passed through the reporter
// agent (universal final-hop user-facing messenger).

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
const PROM_BASE =
    "http://kube-prometheus-stack-prometheus.observability.svc.cluster.local:9090";
const POLL_INTERVAL_MS = 5_000;
const POLL_TIMEOUT_MS = 900_000;

function lgaHeaders(): Record<string, string> {
    const tok = Deno.env.get("HAI_CLI_TOKEN");
    return tok ? { Authorization: `Bearer ${tok}` } : {};
}

// PromQL helper. The agent has empty MCP tools today (see
// `reference_agent_fleet_tool_binding_gap` in memory): every operator
// node uses `with_structured_output()` over `state.content` without
// dynamically querying anything. So this workflow pre-fetches the data
// the agent needs and passes it inline. Path 2 of the tool-binding
// workaround — same shape as storage-weekly / ml-weekly /
// observability-weekly.
//
// All in-cluster queries — kube-prometheus-stack-prometheus is the
// canonical Prom in this cluster; the CNP `cnp-allow.yaml` for windmill
// grants egress to it on port 9090.
async function promQuery(query: string): Promise<unknown> {
    try {
        const r = await fetch(
            `${PROM_BASE}/api/v1/query?query=${encodeURIComponent(query)}`,
            { signal: AbortSignal.timeout(15_000) },
        );
        if (!r.ok) {
            return { error: `HTTP ${r.status}`, query };
        }
        const body = await r.json().catch(() => ({ error: "json parse failed" }));
        return body;
    } catch (e) {
        return { error: (e as Error).message, query };
    }
}

// Format a Prom instant-vector response as a compact human-readable
// list (top N samples by value desc). Agents do better with prose-like
// summaries than raw JSON; raw JSON is included only on errors so the
// human in the loop can debug.
function formatPromInstantVector(
    label: string,
    resp: unknown,
    topN: number = 20,
    valFormat: (v: number) => string = (v) => v.toFixed(2),
): string {
    const r = resp as { status?: string; data?: { result?: Array<{ metric: Record<string, string>; value: [number, string] }> }; error?: string };
    if (r.error) {
        return `### ${label}\n_(error fetching: ${r.error})_\n`;
    }
    if (r.status !== "success" || !r.data?.result) {
        return `### ${label}\n_(unexpected response shape)_\n`;
    }
    const rows = r.data.result;
    if (rows.length === 0) {
        return `### ${label}\n_(no samples)_\n`;
    }
    const sorted = [...rows].sort((a, b) => parseFloat(b.value[1]) - parseFloat(a.value[1]));
    const lines = sorted.slice(0, topN).map((row) => {
        const labels = Object.entries(row.metric)
            .filter(([k]) => k !== "__name__")
            .slice(0, 4)
            .map(([k, v]) => `${k}=${v}`)
            .join(" ");
        return `- \`${labels}\` → ${valFormat(parseFloat(row.value[1]))}`;
    });
    const truncatedNote = rows.length > topN ? `\n_(...${rows.length - topN} more truncated)_` : "";
    return `### ${label}\n${lines.join("\n")}${truncatedNote}\n`;
}

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
    const client_task_id = `network-health-${weekStr}`;

    // Pre-fetch in parallel — see `langgraph-storage-weekly.ts` for
    // the design rationale. Network-flavored signals:
    //   - Cilium BGP session state per neighbor (1=Established,
    //     anything else = down/flap)
    //   - Cert expiry days-remaining for every cert-manager cert
    //   - certificate ready_status (0=NotReady, 1=Ready)
    //   - external-dns last-sync age per controller (sync stalled?)
    //   - external-dns consecutive_soft_errors per controller
    const [
        bgpSessionResp,
        certExpiryResp,
        certReadyResp,
        edSyncAgeResp,
        edSoftErrorsResp,
    ] = await Promise.all([
        // Cilium BGP session state. Values per cilium docs:
        //   1=Idle, 2=Connect, 3=Active, 4=OpenSent, 5=OpenConfirm,
        //   6=Established. Anything != 6 is the surveillance signal.
        promQuery("cilium_bgp_control_plane_session_state"),
        // Days until cert expiry. <30d is the typical lead time
        // cert-manager uses; <14d is "intervene now."
        promQuery(
            "(certmanager_certificate_expiration_timestamp_seconds - time()) / 86400",
        ),
        // 0 = NotReady — the cert-manager reconciler couldn't issue.
        promQuery("certmanager_certificate_ready_status == 0"),
        // external-dns sync staleness. >5 min is a stuck controller.
        promQuery(
            "(time() - external_dns_controller_last_sync_timestamp_seconds) / 60",
        ),
        // external-dns retrying — non-zero means provider error loop.
        promQuery("external_dns_controller_consecutive_soft_errors > 0"),
    ]);

    const evidenceBlock = [
        "## Pre-fetched network evidence (Prometheus snapshot)",
        "",
        formatPromInstantVector(
            "Cilium BGP session state per neighbor (6=Established, else=down/flap)",
            bgpSessionResp,
            20,
            (v) => `state=${v.toFixed(0)}${v === 6 ? " (OK)" : " (NOT ESTABLISHED)"}`,
        ),
        formatPromInstantVector(
            "Cert-manager certs by days-until-expiry (lowest first)",
            certExpiryResp,
            30,
            (v) => `${v.toFixed(1)} days`,
        ),
        formatPromInstantVector(
            "Cert-manager certs in NotReady state (status=0)",
            certReadyResp,
            10,
            (v) => `status=${v.toFixed(0)}`,
        ),
        formatPromInstantVector(
            "external-dns minutes-since-last-sync per controller",
            edSyncAgeResp,
            10,
            (v) => `${v.toFixed(1)} min ago`,
        ),
        formatPromInstantVector(
            "external-dns consecutive soft errors (>0 = retry loop)",
            edSoftErrorsResp,
            10,
            (v) => `${v.toFixed(0)} errors`,
        ),
    ].join("\n");

    const promptBody = [
        "WEEKLY NETWORK HEALTH SWEEP — Class A analysis only.",
        "",
        "Walk Lovenet L1-L7 and report drift / divergence. Anything",
        "you'd recommend changing, list as Class A findings with",
        "evidence — do NOT escalate to Class C/D in this run.",
        "",
        "Coverage:",
        "  1. Omada controller state vs netbox source-of-truth:",
        "     VLAN assignments, port profiles, ACL counts.",
        "  2. APs: any unexpectedly offline, RSSI outliers, channel",
        "     conflicts, firmware drift across the fleet.",
        "  3. Cilium BGP peering: established sessions, route counts,",
        "     any flap/withdrawal events in the last 7 days.",
        "  4. DNS: split-horizon zone integrity, external-dns sync",
        "     state, any orphaned records.",
        "  5. Certificate expiry: anything <30 days from expiry,",
        "     cert-manager renewal state.",
        "  6. SSID configs: VLAN tags + radius profiles + portal",
        "     settings consistent across sites.",
        "",
        "Output: ranked findings (highest-risk first), each with",
        "evidence + one-sentence 'why it matters' + a single",
        "recommended next step. No auto-actions.",
        "",
        "Reference: lovenet-network-configuration repo for canonical",
        "Omada/netbox state; HOMELAB-SPEC for invariants.",
        "",
        evidenceBlock,
    ].join("\n");

    const lgResp = await fetch(`${LG_BASE}/inbox`, {
        method: "POST",
        headers: { ...lgaHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({
            task_id: client_task_id,
            source: "scheduled",
            target_agent: "network-operator",
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
        `Weekly network health sweep complete; network-operator wrote ` +
            `findings to vault. (No prose summary returned; check ` +
            `vault/reports/network-health-${weekStr}.md.)`;

    const email = Deno.env.get("ZULIP_BOT_EMAIL");
    const apiKey = Deno.env.get("ZULIP_BOT_API_KEY");
    if (!email || !apiKey) throw new Error("ZULIP_BOT_EMAIL / ZULIP_BOT_API_KEY env not set");
    const auth = "Basic " + btoa(`${email}:${apiKey}`);
    const zulipApiUrl = Deno.env.get("ZULIP_API_URL") ?? "http://zulip.collab.svc.cluster.local";
    const form = new URLSearchParams({
        type: "stream",
        to: "digests",
        topic: `network-health-${weekStr}`,
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
