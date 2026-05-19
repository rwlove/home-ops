// Triggered by Alertmanager webhook receiver.
//
// Receives a critical alert, asks HolmesGPT to investigate (≤2 tool
// calls, <500 chars), then forwards the summary to Pushover.
//
// Replaces the n8n flow "AlertManager → HolmesGPT → Pushover".

type AlertmanagerPayload = {
    alerts: Array<{
        labels: { alertname: string; severity?: string; namespace?: string; pod?: string };
        annotations: { summary?: string; description?: string };
    }>;
};

export async function main(body: AlertmanagerPayload) {
    const a = body.alerts?.[0];
    if (!a) {
        return { skip: true, reason: "no alerts in payload" };
    }

    const ask = [
        "IMPORTANT: Be concise. Investigate this alert in <=2 tool calls",
        "(kubectl_get_events on the namespace plus one other). Then answer",
        "in <500 chars total: 1 sentence root cause + 1 sentence remediation.",
        "Do not iterate further.",
        "",
        `Alert: ${a.labels.alertname}`,
        `Severity: ${a.labels.severity ?? "unknown"}`,
        `Namespace: ${a.labels.namespace ?? "unknown"}`,
        `Pod: ${a.labels.pod ?? "n/a"}`,
        `Summary: ${a.annotations.summary ?? "(none)"}`,
        `Description: ${a.annotations.description ?? "(none)"}`,
    ].join("\n");

    // HolmesGPT REST: POST /api/chat
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
    const raw = String(data.analysis ?? data.response ?? "").trim();

    let message: string;
    if (!raw) {
        message = "⚠️ HolmesGPT returned no analysis. Check the Windmill execution.";
    } else if (
        /^[:\s]*\{/.test(raw) &&
        /"(suggested_prefixes|tool_call_id|function)"\s*:/.test(raw.slice(0, 200))
    ) {
        message = `⚠️ HolmesGPT model returned a tool-call instead of a summary.`;
    } else if (raw.length <= 1000) {
        message = raw;
    } else {
        const cut = raw.slice(0, 997);
        const m = cut.match(/^[\s\S]*[.!?\n](?=[^.!?\n]*$)/);
        message = ((m && m[0].length > 600) ? m[0] : cut).trimEnd() + "…";
    }

    // Pushover REST: POST /1/messages.json
    const pushoverResp = await sendPushover({
        title: `🔍 ${a.labels.alertname}`,
        message,
        priority: 0,
        sound: "intermission",
    });

    return { alertname: a.labels.alertname, holmes_chars: raw.length, pushover: pushoverResp };
}

async function sendPushover(args: {
    title: string;
    message: string;
    priority: number;
    sound?: string;
}) {
    const token = Deno.env.get("PUSHOVER_APP_TOKEN");
    const user = Deno.env.get("PUSHOVER_USER_KEY");
    if (!token || !user) {
        throw new Error("PUSHOVER_APP_TOKEN / PUSHOVER_USER_KEY env not set");
    }
    const form = new URLSearchParams({
        token,
        user,
        title: args.title,
        message: args.message,
        priority: String(args.priority),
    });
    if (args.sound) form.set("sound", args.sound);
    const r = await fetch("https://api.pushover.net/1/messages.json", {
        method: "POST",
        body: form,
        signal: AbortSignal.timeout(30_000),
    });
    return { status: r.status };
}
