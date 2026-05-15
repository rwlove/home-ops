# n8n workflows — git-tracked sources of truth

Workflows are stored encrypted in n8n's postgres at runtime. This dir
holds JSON exports so they survive a postgres rebuild.

## Re-importing after a rebuild

1. Restore Pushover (and any other) credentials in n8n first (UI: **Credentials → Create**, types: `pushoverApi`, etc.). Note the new credential IDs.

2. Edit the workflow JSON to update credential references and any
   placeholders:
   - Pushover node `parameters.userKey` is sanitized to
     `<REPLACE_FROM_OP_AT_IMPORT_TIME>` — replace with the value from
     1Password item `Pushover` field `userkey`.
   - If a node has `credentials.pushoverApi.id`, replace with the new
     credential ID created in step 1.

3. Import via API:

   ```sh
   PT=$(op read "op://Kubernetes/Pushover/userkey")
   sed -i "s|<REPLACE_FROM_OP_AT_IMPORT_TIME>|$PT|" alertmanager-holmesgpt-pushover.json
   curl -X POST -H "X-N8N-API-KEY: $N8N_KEY" -H "Content-Type: application/json" \
     -d @alertmanager-holmesgpt-pushover.json \
     https://n8n.${SECRET_DOMAIN}/api/v1/workflows
   curl -X POST -H "X-N8N-API-KEY: $N8N_KEY" \
     https://n8n.${SECRET_DOMAIN}/api/v1/workflows/<new-id>/activate
   ```

## Updating after edits in the n8n UI

When you edit a workflow in the n8n UI, re-export to keep git in sync:

```sh
kubectl exec -n mcp-system deploy/n8n-mcp -c app -- node -e "
fetch(process.env.N8N_API_URL + '/api/v1/workflows/<workflow-id>', {
  headers: { 'X-N8N-API-KEY': process.env.N8N_API_KEY }
}).then(r => r.json()).then(wf => {
  const out = {};
  for (const k of ['name','nodes','connections','settings']) if (wf[k]) out[k] = wf[k];
  for (const n of out.nodes||[]) {
    delete n.id; delete n.webhookId;
    if (n.type === 'n8n-nodes-base.pushover' && n.parameters?.userKey) n.parameters.userKey = '<REPLACE_FROM_OP_AT_IMPORT_TIME>';
  }
  console.log(JSON.stringify(out, null, 2));
});" > workflows/<file>.json
```

## Workflows here

- **alertmanager-holmesgpt-pushover.json** — receives AlertManager
  webhook for `severity=critical` alerts, calls HolmesGPT for a concise
  root-cause summary, sends as a second Pushover notification with
  prefix `🔍 <alertname>`. Wired from
  `kubernetes/apps/observability/kube-prometheus-stack/app/alertmanagerconfig.yaml`
  via the `n8n-investigate` receiver pointing at the in-cluster n8n
  webhook URL.

### LangGraph agent fleet (phase 9 of `project_langgraph_redesign`)

These five workflows wire `langgraph-agents` to its external surfaces (Zulip
notifications, Pushover Tier 1, scheduled cron tasks). They depend on the
`langgraph-agents` Deployment being live, which is gated by
`kubernetes/apps/ai/langgraph-agents/` reconciling. The Zulip-API and
Pushover credentials must exist in n8n before import.

- **langgraph-inbox.json** — public webhook `/webhook/langgraph-inbox`. Body:
  `{ task_id?, source, content, user? }`. Normalizes payload, POSTs to
  `langgraph-agents:/inbox`, branches on `status=paused` to forward to the
  approval-post workflow. Used by voice-transcription pipelines + Zulip
  `#inbox` outgoing webhooks.

- **langgraph-approval-post.json** — internal webhook
  `/webhook/langgraph-approval-post`. Posts the approval request to Zulip
  stream `#approvals` (topic = `<task_id> — Class C: <action>`) and fires
  a Tier-1 Pushover for attention. Receives the structured
  `paused_for.approval_request` from `langgraph-inbox`.

- **langgraph-approval-receive.json** — Zulip outgoing-webhook target. Fires
  on emoji reactions in `#approvals`. Verifies the reactor is Rob (compares
  `body.user_id` against env `ROB_ZULIP_USER_ID`). Parses the topic for
  `task_id` + `Class` + `target=server.method`. Maps emoji 👍/👎/⏸️ to
  approve/reject/defer. HMAC-signs a token using env
  `LANGGRAPH_APPROVAL_SIGNING_KEY`, POSTs to `langgraph-agents:/approval`
  to resume the paused workflow.

- **langgraph-awaiting-user-sweep.json** — schedule every 5 minutes. GETs
  `/admin/tasks`, filters to paused tasks past 30min / 4h / 7d thresholds.
  30min → secondary Pushover. 4h → mark cold via `/admin/tasks/<id>/timeout-tier`.
  7d → auto-cancel via `/admin/tasks/<id>/cancel` (langgraph-agents needs
  to implement these admin endpoints in phase 5+).

- **langgraph-daily-digest.json** — schedule daily at 22:00 America/New_York.
  POSTs an inbox entry asking for today's digest; the triager routes to the
  `reporter` agent, which writes `~/vaults/claude/reports/daily-YYYY-MM-DD.md`.
  Posts a summary to Zulip stream `#digests` topic `daily-YYYY-MM-DD`.

- **langgraph-cost-cap-watcher.json** — schedule every 4 hours. GETs
  `/admin/costs/today` (planned endpoint; gated by Langfuse deployment in
  phase 5). At 80% → Tier 2 Pushover warning. At 100% → Tier 1 with
  `cost_cap_hit` flag flipped (langgraph-agents refuses Claude-API-eligible
  specialists until the next daily reset).

## New credentials needed (in n8n UI) for the LangGraph workflows

| Credential type | Name | Used by |
|---|---|---|
| `pushoverApi` | "Pushover - alertmanager" (exists) | approval-post, awaiting-user-sweep, cost-cap-watcher |
| `httpBasicAuth` | "Zulip API - n8n-bot" (new) | approval-post, daily-digest |

The Zulip credential is an HTTP Basic Auth with username = bot email and
password = bot API key. Provision via the Zulip admin UI as part of phase 10
bot setup; until then, the workflows can be imported but won't post to Zulip.

## New n8n env vars (set via the HelmRelease ExternalSecret)

| Env var | Source | Used by |
|---|---|---|
| `ROB_ZULIP_USER_ID` | Zulip admin → user profile → numeric ID | approval-receive (impersonation defense) |
| `LANGGRAPH_APPROVAL_SIGNING_KEY` | 1Password (also injected into langgraph-agents) | approval-receive (HMAC signing) |

The signing key MUST be identical between n8n and langgraph-agents. Generate
once with `openssl rand -hex 32` and store both targets.
