// Manually-triggered smoke test of the errand-runner approval flow.
//
// **What this validates:**
//   The HMAC-verified resume token from Windmill TS
//   (`signApprovalToken` in `langgraph-inbox.ts`) round-trips correctly
//   into errand-runner's Python `_verify_approval_token`. A silent
//   drift between the two ends — wrong secret, wrong algorithm, wrong
//   field order — would today look like a successful approval push
//   while every Class-C action proposed by every operator silently
//   fails to execute.
//
// **What this does:**
//   1. POST /admin/smoke/start-approval — kicks off a smoke graph run
//      that interrupts at errand-runner with a synthetic ApprovalRequest
//      targeting `smoke.test_write`.
//   2. The existing langgraph-approval-post Windmill sweep sees the
//      interrupt, signs a magic-link token, and pushes it to the
//      operator's phone via ntfy.
//   3. Operator taps the magic link → /approval webhook resumes the
//      graph → errand-runner runs its smoke-execution branch (filesystem
//      write → readback → delete inside `vault_smoke_dir`).
//   4. We poll /admin/tasks/<task_id> until the checkpointer shows the
//      graph reached END with `output` populated.
//   5. Parse the JSON result envelope and return it to Windmill UI.
//
// **Trigger:**
//   Manual only — operator-fired from the Windmill UI when validating
//   the approval flow. NOT a cron. If we ever want a weekly regression
//   variant, the same script could run with an additional
//   service-account magic-link auto-tap (separate flow, future PR).
//
// **Timeout discipline:**
//   The poll loop waits 10 minutes for the operator to tap the magic
//   link. If they don't, the flow fails loudly so the operator knows
//   the smoke didn't complete. Don't bump this without thinking — a
//   forgotten unfinished smoke leaves an approval interrupt sitting in
//   the production checkpointer forever.

type SmokeStartResp = {
    task_id?: string;
    status?: string;
    note?: string;
};

type AdminTaskResp = {
    task_id?: string;
    checkpointer?: {
        values?: { output?: string };
        next?: string[];
        interrupts?: { id: string; value?: Record<string, unknown> | null }[];
    };
};

type SmokeTimings = {
    hmac_verify_ms: number;
    fs_write_ms: number;
    fs_readback_ms: number;
    fs_delete_ms: number;
    smoke_total_ms: number;
};

type SmokeResult = {
    path: string;
    expected_content: string;
    actual_content: string | null;
    write_ok: boolean;
    readback_ok: boolean;
    delete_ok: boolean;
    file_gone_after_delete: boolean;
    hmac_verify_ok: boolean;
    timings: SmokeTimings;
};

const LG_BASE = "http://langgraph-agents.ai.svc.cluster.local:8765";
const POLL_INTERVAL_MS = 3_000;
const POLL_TIMEOUT_MS = 600_000; // 10 min — operator-tap latency budget

// HAI_CLI_TOKEN gates /admin/* on langgraph-agents (Bearer-auth
// middleware introduced in 0.2.38). Same env every other LGA-calling
// workflow uses; absent token → server-side falls back to allow-all
// in dev mode, so the smoke still runs locally.
function lgaHeaders(): Record<string, string> {
    const tok = Deno.env.get("HAI_CLI_TOKEN");
    return tok ? { Authorization: `Bearer ${tok}` } : {};
}

export async function main(label?: string) {
    const safeLabel = (label ?? "manual")
        .replace(/[^a-zA-Z0-9_-]/g, "")
        .slice(0, 32) || "manual";

    const t0_request_start = Date.now();

    // Step 1: kick off the smoke run. Endpoint returns once the graph
    // has hit the errand-runner interrupt (paused, waiting for /approval).
    const startResp = await fetch(`${LG_BASE}/admin/smoke/start-approval`, {
        method: "POST",
        headers: { ...lgaHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ label: safeLabel }),
        // 30s is generous — the endpoint should return in <1s once the
        // graph hits the interrupt. If it takes longer something is
        // wrong with the smoke graph compile / checkpointer.
        signal: AbortSignal.timeout(30_000),
    });

    if (!startResp.ok) {
        const errBody = await startResp.text().catch(() => "(no body)");
        throw new Error(
            `smoke start failed: HTTP ${startResp.status} — ${errBody.slice(0, 500)}`,
        );
    }

    const start: SmokeStartResp = (await startResp.json().catch(() => ({}))) as SmokeStartResp;
    const task_id = start.task_id;
    if (!task_id) {
        throw new Error(`smoke start missing task_id: ${JSON.stringify(start)}`);
    }

    const t1_interrupt_reached = Date.now();
    const time_to_interrupt_ms = t1_interrupt_reached - t0_request_start;

    // Step 2: operator should now be receiving the ntfy push. Poll the
    // checkpointer until the graph reaches END with `output` set.
    //
    // Smoke runs do NOT go through the queue substrate — the smoke
    // graph is invoked directly inside /admin/smoke/start-approval. So
    // we don't read `body.queue` here, only the checkpointer state:
    //
    //   - `checkpointer.next: []` AND `checkpointer.values.output` set
    //     → graph reached END; parse the envelope and return
    //   - `checkpointer.interrupts.length > 0`
    //     → still paused waiting for the operator's approval tap
    //   - `checkpointer.next: ["errand-runner"]` AND no interrupt
    //     → graph resumed but not yet finished (typically <1s after tap)
    const envelope = await pollForSmokeResult(task_id);

    const t3_complete = Date.now();

    // Sanity-check the load-bearing assertion. If hmac_verify_ok is
    // FALSE while we got here, errand-runner's branch logic accepted
    // the token differently than expected — but the JSON serializer
    // still ran, so we see the result. Treat as failure.
    const hmac_drift_caught = envelope.hmac_verify_ok === false;

    return {
        task_id,
        outcome: hmac_drift_caught
            ? "HMAC_DRIFT_DETECTED"
            : envelope.write_ok && envelope.readback_ok && envelope.delete_ok
            ? "OK"
            : "FS_FAILURE",
        windmill_side_timings_ms: {
            time_to_interrupt: time_to_interrupt_ms,
            total_wall_clock: t3_complete - t0_request_start,
        },
        smoke_envelope: envelope,
    };
}

async function pollForSmokeResult(taskId: string): Promise<SmokeResult> {
    const deadline = Date.now() + POLL_TIMEOUT_MS;
    let lastPhase = "waiting-for-task";

    while (Date.now() < deadline) {
        const r = await fetch(
            `${LG_BASE}/admin/tasks/${encodeURIComponent(taskId)}`,
            {
                signal: AbortSignal.timeout(15_000),
                headers: lgaHeaders(),
            },
        );

        if (r.status === 404) {
            // Race — checkpointer hasn't materialized the thread yet.
            // Rare; retry briefly.
            await sleep(POLL_INTERVAL_MS);
            continue;
        }

        if (!r.ok) {
            throw new Error(
                `/admin/tasks/${taskId} returned HTTP ${r.status}`,
            );
        }

        const body = (await r.json().catch(() => ({}))) as AdminTaskResp;
        const cp = body.checkpointer;

        if (!cp) {
            // Checkpointer state not populated yet; another brief wait.
            await sleep(POLL_INTERVAL_MS);
            continue;
        }

        const interrupts = cp.interrupts ?? [];
        const next = cp.next ?? [];
        const output = cp.values?.output;

        // Terminal: graph reached END (no next nodes) AND output is set.
        if (next.length === 0 && typeof output === "string" && output.length > 0) {
            // Parse the JSON envelope. errand-runner's _run_smoke
            // serializes a SmokeResult via model_dump_json().
            let parsed: SmokeResult;
            try {
                parsed = JSON.parse(output) as SmokeResult;
            } catch (e) {
                throw new Error(
                    `smoke output is not valid JSON. Raw: ${output.slice(0, 500)}. ` +
                        `Parse error: ${(e as Error).message}`,
                );
            }
            return parsed;
        }

        // Non-terminal phase reporting — useful for debugging if the
        // smoke hangs. Windmill captures each phase change as a log
        // line (one event per state transition, not per poll).
        const phase = interrupts.length > 0
            ? "paused-for-approval"
            : next.length > 0
            ? `resumed-running:${next.join(",")}`
            : "no-state-yet";

        if (phase !== lastPhase) {
            console.log(`[smoke] task=${taskId} phase=${phase}`);
            lastPhase = phase;
        }

        await sleep(POLL_INTERVAL_MS);
    }

    throw new Error(
        `smoke poll timeout after ${POLL_TIMEOUT_MS}ms — ` +
            `task ${taskId} still in phase "${lastPhase}". ` +
            `Check ntfy for the approval push; tap the magic link to resume. ` +
            `Re-running this flow will create a new smoke task (the abandoned ` +
            `one sits in the checkpointer indefinitely — clean via ` +
            `POST /admin/tasks/${taskId}/cancel if desired).`,
    );
}

function sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
}
