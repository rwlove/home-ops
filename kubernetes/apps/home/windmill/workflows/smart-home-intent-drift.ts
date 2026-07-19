// Weekly audit of the smart-home device intent map vs HA reality.
//
// The intent map lives at
// agents/workspaces/smart-home-operator/device-intent-map.yaml in the
// langgraph-agents repo. It carries the semantic layer (HACS integration
// opinions, critical-device context, severity maps) that HA itself can't
// provide.
//
// This workflow fires an audit task at the smart-home-operator agent —
// which already has ha-mcp access and loads the intent map at task start.
// The agent compares the map to current HA state and reports gaps:
//
//   1. HACS integrations installed in HA but absent from the map
//      → suggest adding when_to_use guidance
//   2. Integrations in the map but not installed in HA
//      → rot, remove the entry
//   3. Critical-class entities (leak sensors, locks in named areas, etc.)
//      not in critical_devices
//      → suggest adding context + escalation
//   4. critical_devices entries pointing at non-existent entities
//      → rot, remove or fix
//
// Output: Email to ADMIN if drift_count > 0. Silent no-op if clean.
// Schedule: weekly (configured via Windmill cron — not part of this file).

import * as wmill from "npm:windmill-client@1.527.0";

const LG_BASE = "http://langgraph-agents.ai.svc.cluster.local:8765";

function lgaHeaders(): Record<string, string> {
    const tok = Deno.env.get("HAI_CLI_TOKEN");
    return tok ? { Authorization: `Bearer ${tok}` } : {};
}

async function hashString(s: string): Promise<string> {
    const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
    return Array.from(new Uint8Array(buf))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
}

const POLL_INTERVAL_MS = 10_000;
const POLL_TIMEOUT_MS = 1_200_000; // 20 min — agent uses ha-mcp + LLM

function sleep(ms: number): Promise<void> {
    return new Promise((r) => setTimeout(r, ms));
}

async function pollForOutput(taskId: string): Promise<string | null> {
    const deadline = Date.now() + POLL_TIMEOUT_MS;
    while (Date.now() < deadline) {
        await sleep(POLL_INTERVAL_MS);
        const r = await fetch(
            `${LG_BASE}/admin/tasks/${encodeURIComponent(taskId)}`,
            { headers: lgaHeaders(), signal: AbortSignal.timeout(15_000) },
        );
        if (!r.ok) {
            if (r.status === 404) continue;
            throw new Error(`/admin/tasks returned ${r.status}`);
        }
        const j = await r.json();
        const status = j?.queue?.status;
        if (status === "done") {
            return (j?.queue?.result?.output as string | undefined) ?? null;
        }
        if (status === "failed" || status === "dlq") {
            return null;
        }
        // pending / claimed — keep polling
    }
    return null;
}

const PROMPT = `Audit your device intent map (loaded into your prompt at the
top of this task) against current Home Assistant reality. Use ha-mcp
to enumerate what HA exposes. Report drift in four categories:

1. HACS integrations installed in HA but absent from \`hacs_integrations\`
   in the intent map — list with one-line "what does this do" hints.
2. \`hacs_integrations\` entries pointing at integrations NOT installed
   in HA (rot — recommend removing).
3. Critical-class entities (leak sensors, safety-rated locks/switches,
   key climate/network gear) NOT in \`critical_devices\` — list with
   entity_id + suggested context.
4. \`critical_devices\` entries whose \`entity\` field doesn't resolve
   to an existing HA entity (rot — recommend fixing or removing).

If all four categories are empty, return EXACTLY: "Intent map: in sync."
Otherwise, group findings under those four headings. Be terse.

Don't propose YAML edits — the human edits the map. Just report gaps.`;

export async function main() {
    const taskId = `smart-home-intent-drift-${new Date().toISOString().slice(0, 10)}`;

    const inboxResp = await fetch(`${LG_BASE}/inbox`, {
        method: "POST",
        headers: { ...lgaHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({
            task_id: taskId,
            source: "cli",
            user: "rob",
            content: PROMPT,
            // Pin to smart-home-operator — the agent that loads the intent
            // map and has ha-mcp access. Triager would mis-route this.
            target_agent: "smart-home-operator",
            idempotency_key: taskId,
        }),
        signal: AbortSignal.timeout(15_000),
    });
    if (!inboxResp.ok) {
        const body = await inboxResp.text().catch(() => "");
        return { error: `/inbox returned ${inboxResp.status}`, body: body.slice(0, 400) };
    }

    const output = await pollForOutput(taskId);
    if (!output) {
        return { error: "agent task didn't complete within budget", task_id: taskId };
    }

    // "Intent map: in sync." → silent no-op.
    if (output.trim().startsWith("Intent map: in sync")) {
        return {
            task_id: taskId,
            skip: true,
            reason: "intent map is in sync",
        };
    }

    // Dedup via Windmill setState — only DM if the verdict text changes
    // from the prior run. Identical drift week-over-week means ADMIN
    // already saw the report and hasn't acted yet; no need to nag.
    const verdictHash = await hashString(output);
    const prevHash = (await wmill.getState()) as string | null;
    if (prevHash === verdictHash) {
        return {
            task_id: taskId,
            skip: true,
            reason: "drift unchanged from prior run",
        };
    }
    await wmill.setState(verdictHash);

    await sendEmail(
        "⚠️ Smart-home intent-map drift detected",
        [
            `device-intent-map.yaml needs attention.`,
            ``,
            output,
            ``,
            `Audited by smart-home-operator. Task: ${taskId}.`,
        ].join("\n"),
    );

    return {
        task_id: taskId,
        output_chars: output.length,
        drift_reported: true,
    };
}

// ---------- Email (in-cluster smtp-relay -> Mailgun) ----------
//
// Raw SMTP over plaintext to smtp-relay:2525 (maddy, `tls off`, no auth
// on submission — it relays everything out via Mailgun). Kept
// self-contained per the workflows "no shared modules" convention.

async function sendEmail(subject: string, body: string) {
    const host = Deno.env.get("SMTP_HOST") ?? "smtp-relay.home.svc.cluster.local";
    const port = parseInt(Deno.env.get("SMTP_PORT") ?? "2525", 10);
    const from = Deno.env.get("NOTIFY_EMAIL_FROM");
    const to = Deno.env.get("NOTIFY_EMAIL_TO");
    if (!from) throw new Error("NOTIFY_EMAIL_FROM env not set");
    if (!to) throw new Error("NOTIFY_EMAIL_TO env not set");

    const conn = await Deno.connect({ hostname: host, port });
    const enc = new TextEncoder();
    const dec = new TextDecoder();
    const rbuf = new Uint8Array(4096);

    // Read one full SMTP reply. Multi-line replies use "NNN-" for
    // continuation lines and "NNN " (space) on the final line.
    async function reply(): Promise<{ code: number; text: string }> {
        let acc = "";
        while (true) {
            const n = await conn.read(rbuf);
            if (n === null) break;
            acc += dec.decode(rbuf.subarray(0, n));
            const last = acc.split(/\r?\n/).filter((l) => l.length > 0).at(-1) ?? "";
            if (/^\d{3} /.test(last)) break;
        }
        return { code: parseInt(acc.slice(0, 3), 10) || 0, text: acc.trim() };
    }
    async function cmd(line: string, expect: number) {
        await conn.write(enc.encode(line + "\r\n"));
        const { code, text } = await reply();
        if (code !== expect) {
            throw new Error(`SMTP ${line.split(/\s/)[0]}: expected ${expect}, got ${text}`);
        }
    }

    try {
        const greet = await reply(); // 220 banner
        if (greet.code !== 220) throw new Error(`SMTP greeting: ${greet.text}`);
        await cmd(`EHLO windmill`, 250);
        await cmd(`MAIL FROM:<${from}>`, 250);
        await cmd(`RCPT TO:<${to}>`, 250);
        await cmd(`DATA`, 354);
        const msg =
            `From: ${from}\r\n` +
            `To: ${to}\r\n` +
            `Subject: ${subject}\r\n` +
            `Content-Type: text/plain; charset=utf-8\r\n` +
            `\r\n` +
            body.replace(/\r?\n/g, "\r\n").replace(/^\./gm, "..") +
            `\r\n.`;
        await cmd(msg, 250);
        await cmd(`QUIT`, 221);
    } finally {
        conn.close();
    }
}
