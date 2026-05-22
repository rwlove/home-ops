// Triggered by Alertmanager webhook receiver.
//
// Receives a critical alert, asks HolmesGPT to investigate (≤2 tool
// calls, <500 chars), then forwards the summary via ntfy → #alerts.
//
// Replaces the n8n flow "AlertManager → HolmesGPT → Pushover".

type AlertmanagerAlert = {
    labels: { alertname: string; severity?: string; namespace?: string; pod?: string };
    annotations: { summary?: string; description?: string };
};

// Windmill maps top-level JSON keys of the webhook body to function
// args by name — it does NOT wrap the whole body into a single
// parameter. Alertmanager POSTs `{"alerts": [...], "version": "4",
// "groupKey": "...", ...}`; Windmill invokes
// `main(alerts=[...], version="4", ...)`. Take `alerts` directly.
// See memory `project_n8n_to_windmill_migration_done.md`.
export async function main(alerts?: AlertmanagerAlert[]) {
    const a = alerts?.[0];
    if (!a) {
        return { skip: true, reason: "no alerts in payload" };
    }

    // Constrained prompt — the earlier shape ("answer in <500 chars
    // total") wasn't enough to keep qwen2.5:32b from emitting a
    // raw tool-call JSON as its "answer," which the workflow detects
    // as `MCP error -32602`-style output and surfaces as "model
    // returned a tool-call instead of a summary." Tightening:
    //
    //  - Explicit budget on tool calls (at most 2).
    //  - Explicit ban on additional tool calls after the budget.
    //  - Format pinned to a 2-line prose answer (root cause +
    //    remediation), forbidding JSON / structured output / further
    //    tool invocations in the final response.
    //
    // Keep it short; long prompts are themselves a budget problem
    // (Holmes' system prompt + tool descriptions are already ~16K
    // input tokens before this `ask`).
    const ask = [
        "Investigate this alert. Use AT MOST 2 tool calls",
        "(typically kubectl_get_events on the namespace plus one other).",
        "",
        "After the 2nd tool returns, you MUST produce your final answer",
        "as plain text — NOT another tool call, NOT JSON, NOT structured",
        "output. Stop calling tools and write prose.",
        "",
        "Final answer format (<500 characters total, two sentences max):",
        "  1) one sentence on the most likely root cause",
        "  2) one sentence on the remediation",
        "",
        "Do not output any JSON. Do not output any tool_call objects.",
        "Do not iterate further. Respond in prose only.",
        "",
        `Alert: ${a.labels.alertname}`,
        `Severity: ${a.labels.severity ?? "unknown"}`,
        `Namespace: ${a.labels.namespace ?? "unknown"}`,
        `Pod: ${a.labels.pod ?? "n/a"}`,
        `Summary: ${a.annotations.summary ?? "(none)"}`,
        `Description: ${a.annotations.description ?? "(none)"}`,
    ].join("\n");

    // HolmesGPT REST: POST /api/chat
    const raw = await askHolmes(ask);

    let message: string;
    if (!raw) {
        message = "⚠️ HolmesGPT returned no analysis. Check the Windmill execution.";
    } else if (isToolCallShape(raw)) {
        // qwen2.5:32b at the tool-loop's max_steps sometimes returns a
        // raw tool_call JSON instead of synthesizing prose. PR #11973
        // tried to fix this from the prompt side; didn't take. Now:
        // (1) surface WHAT Holmes wanted to call so the operator gets
        //     an actionable hint instead of a useless warning, and
        // (2) re-call Holmes with a no-tools follow-up asking only
        //     for a prose best-guess from the alert metadata.
        const hint = describeToolCall(raw);
        const fallback = await askHolmes(noToolsPrompt(a));
        if (fallback && !isToolCallShape(fallback)) {
            message = `${fallback}\n\n_(Holmes' tool-loop wanted to: ${hint})_`;
        } else {
            message = `⚠️ Holmes investigation incomplete — wanted to call ${hint}. ` +
                `Re-prompt for prose also failed. Investigate manually.`;
        }
    } else if (raw.length <= 1000) {
        message = raw;
    } else {
        const cut = raw.slice(0, 997);
        const m = cut.match(/^[\s\S]*[.!?\n](?=[^.!?\n]*$)/);
        message = ((m && m[0].length > 600) ? m[0] : cut).trimEnd() + "…";
    }

    const sev = (a.labels.severity ?? "").toLowerCase();
    const priority = sev === "critical" ? 5 : sev === "warning" ? 4 : 3;

    const notifyResp = await publishNtfy({
        topic: "alerts",
        title: `🔍 ${a.labels.alertname}`,
        message,
        priority,
        tags: ["mag"],
    });

    return { alertname: a.labels.alertname, holmes_chars: raw.length, ntfy: notifyResp };
}

// ---------- Holmes helpers ----------

async function askHolmes(ask: string): Promise<string> {
    const hg = await fetch(
        "http://holmesgpt.observability.svc.cluster.local:8080/api/chat",
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ask }),
            signal: AbortSignal.timeout(600_000),
        },
    );
    const data = await hg.json().catch(() => ({}));
    return String(data.analysis ?? data.response ?? "").trim();
}

function isToolCallShape(raw: string): boolean {
    return /^[:\s]*\{/.test(raw) &&
        /"(suggested_prefixes|tool_call_id|function)"\s*:/.test(raw.slice(0, 200));
}

// Parse the tool_call JSON Holmes returned and produce a human-readable
// one-liner describing what tool it wanted to invoke. The shape varies
// by model — qwen2.5 OpenAI-compat returns
//   `{"function": {"name": "foo", "arguments": "{...}"}}`
// or the wrapped streaming form
//   `{"tool_calls":[{"function":{"name":"foo","arguments":"{...}"}}]}`
// — fall back to the bare alertname when nothing parses.
function describeToolCall(raw: string): string {
    try {
        const j = JSON.parse(raw);
        const fn = j?.function ?? j?.tool_calls?.[0]?.function ?? j?.suggested_prefixes;
        if (typeof fn === "string") return fn; // suggested_prefixes shape
        if (fn?.name) {
            const args = typeof fn.arguments === "string" ? fn.arguments : JSON.stringify(fn.arguments ?? {});
            return `\`${fn.name}(${args.slice(0, 120)})\``;
        }
    } catch (_) { /* ignore JSON errors — fall through */ }
    return "another tool (couldn't parse)";
}

// No-tools follow-up prompt — used when the first call returns a
// tool_call shape. We can't continue the prior investigation (Holmes
// /api/chat is stateless across calls), so this asks for a prose
// best-guess from the alert metadata only.
function noToolsPrompt(a: AlertmanagerAlert): string {
    return [
        "Earlier my investigation didn't synthesize a prose summary.",
        "DO NOT call any tools. Respond ONLY in plain English prose.",
        "Based ONLY on the alert metadata below, give 1 sentence on the",
        "most likely root cause and 1 sentence on the remediation.",
        "<300 characters total. No JSON. No tool calls.",
        "",
        `Alert: ${a.labels.alertname}`,
        `Severity: ${a.labels.severity ?? "unknown"}`,
        `Namespace: ${a.labels.namespace ?? "unknown"}`,
        `Pod: ${a.labels.pod ?? "n/a"}`,
        `Summary: ${a.annotations.summary ?? "(none)"}`,
        `Description: ${a.annotations.description ?? "(none)"}`,
    ].join("\n");
}

async function publishNtfy(args: {
    topic: string;
    title: string;
    message: string;
    priority?: number;
    tags?: string[];
}) {
    const url = Deno.env.get("NTFY_URL") ?? "https://ntfy.thesteamedcrab.com";
    const token = Deno.env.get("NTFY_WRITE_TOKEN");
    if (!token) throw new Error("NTFY_WRITE_TOKEN env not set");
    const r = await fetch(url, {
        method: "POST",
        headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
        },
        body: JSON.stringify({
            topic: args.topic,
            title: args.title,
            message: args.message,
            priority: args.priority ?? 3,
            tags: args.tags ?? [],
        }),
        signal: AbortSignal.timeout(30_000),
    });
    return { status: r.status, ok: r.ok };
}
