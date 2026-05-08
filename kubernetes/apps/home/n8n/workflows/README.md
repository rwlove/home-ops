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
   ```
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

```
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
