// Cron: weekly, Saturday 03:00 America/New_York (routine maintenance window).
//
// Fires the `observability-operator` agent on a weekly Class-A
// observability stack health sweep. PrometheusRule drift, alert
// flap-replay, silence audit, dashboard breakage, Loki ingest
// posture, HolmesGPT prompt-tuning signals.
//
// Class A only. Any Class C action (rule edit, silence creation,
// HolmesGPT system-prompt change) routes through errand-runner with
// the operator's prime-directive gate: cannot bury a real alert
// under flap.
//
// Pinned `target_agent: "observability-operator"`.
//
// Result lands in Zulip stream #digests, topic
// `observability-health-YYYY-Www`.

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

// PromQL helper — same shape as the other Path-2-enriched weekly
// crons (see [[reference_agent_fleet_tool_binding_gap]]).
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
    const client_task_id = `observability-health-${weekStr}`;

    // Pre-fetch observability-stack state in parallel. The agent has
    // no MCP tool bindings; without this evidence the observability-
    // operator can't actually inspect what it's asked to inspect.
    // See [[reference_agent_fleet_tool_binding_gap]].
    const [
        firingAlertsResp,
        activeSilencesResp,
        scrapeDownResp,
        lokiIngestRateResp,
        prometheusSdTargetsResp,
    ] = await Promise.all([
        // Currently-firing alerts grouped by alertname — flap signal
        promQuery('ALERTS{alertstate="firing"}'),
        // Active alertmanager silences (which may be hiding real alerts)
        promQuery('alertmanager_silences{state="active"}'),
        // Scrape targets DOWN cluster-wide — catches monitor coverage gaps
        promQuery('up == 0'),
        // Loki ingest rate (bytes/sec, last 5m) by stream
        promQuery('rate(loki_distributor_bytes_received_total[5m])'),
        // Prometheus SD discovery — orphaned targets that should be cleaned up
        promQuery('prometheus_sd_kubernetes_state'),
    ]);

    const evidenceBlock = [
        "## Pre-fetched cluster evidence (Prometheus snapshot)",
        "",
        formatPromInstantVector(
            "Firing alerts (right now)",
            firingAlertsResp,
            30,
        ),
        formatPromInstantVector(
            "Active silences",
            activeSilencesResp,
            20,
        ),
        formatPromInstantVector(
            "Scrape targets DOWN (should be empty)",
            scrapeDownResp,
            30,
        ),
        formatPromInstantVector(
            "Loki ingest rate (bytes/sec, top 10 streams)",
            lokiIngestRateResp,
            10,
            (v) => `${(v / 1024).toFixed(1)} KB/s`,
        ),
        formatPromInstantVector(
            "Prometheus SD state (orphan-target signal)",
            prometheusSdTargetsResp,
            5,
        ),
    ].join("\n");

    const promptBody = [
        "WEEKLY OBSERVABILITY STACK HEALTH SWEEP — Class A only.",
        "",
        "Walk the prom-stack + loki + grafana + alertmanager +",
        "HolmesGPT and report drift. List anything you'd recommend",
        "changing as Class A findings — do NOT escalate in this run.",
        "",
        "Coverage:",
        "  1. PrometheusRule firings: top flap offenders over the",
        "     last 7d (alerts firing+resolving >3x/day). Candidates",
        "     for tuning, silencing, or removal.",
        "  2. AlertManager silences: any active silences past their",
        "     intended TTL? Any silence with no comment / no owner?",
        "     Any silence covering a NEW alert that just started",
        "     firing under it?",
        "  3. ServiceMonitor / PodMonitor / Probe / ScrapeConfig:",
        "     any defined-but-not-scraping? Any scrape target down",
        "     >24h?",
        "  4. Loki: ingest rate trend, query latency, retention",
        "     state, any namespace producing log volume outliers.",
        "  5. Grafana dashboards: any with broken panels (datasource",
        "     missing, query errors in recent renders)?",
        "  6. HolmesGPT prompt-tuning signals: alert-to-rootCause",
        "     success rate per the prompt-surgery PR (#12016). Empty-",
        "     rootCause cards still showing up? Tool-loop-wanted",
        "     trailers? Average tool-call count vs the new budget of 6.",
        "",
        "Output: ranked findings (highest-flap-or-coverage-gap first),",
        "each with evidence + 'why it matters' + a single recommended",
        "next step. No auto-actions.",
        "",
        "Reference: HOMELAB-SPEC observability-operator prime",
        "directive (cannot bury a real alert under flap).",
        "",
        evidenceBlock,
    ].join("\n");

    const lgResp = await fetch(`${LG_BASE}/inbox`, {
        method: "POST",
        headers: { ...lgaHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({
            task_id: client_task_id,
            source: "scheduled",
            target_agent: "observability-operator",
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
        `Weekly observability health sweep complete; observability-` +
            `operator wrote findings to vault. (No prose summary; ` +
            `check vault/reports/observability-health-${weekStr}.md.)`;

    const email = Deno.env.get("ZULIP_BOT_EMAIL");
    const apiKey = Deno.env.get("ZULIP_BOT_API_KEY");
    if (!email || !apiKey) throw new Error("ZULIP_BOT_EMAIL / ZULIP_BOT_API_KEY env not set");
    const auth = "Basic " + btoa(`${email}:${apiKey}`);
    const zulipApiUrl = Deno.env.get("ZULIP_API_URL") ?? "http://zulip.collab.svc.cluster.local";
    const form = new URLSearchParams({
        type: "stream",
        to: "digests",
        topic: `observability-health-${weekStr}`,
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
