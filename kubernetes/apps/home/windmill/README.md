# Windmill

FOSS workflow automation tool, replacing. See the migration plan in
`docs/src/audit/` and the steady-marshmallow plan at
`~/.claude-personal/plans/i-want-you-to-steady-marshmallow.md`.

## Activation (Phase 1a — no SSO yet)

Initial deploy uses Windmill's native admin auth. Authelia OIDC SSO via
oauth2-proxy is deferred to Phase 1b (separate PR) to avoid editing
the cluster's 24-OIDC-client Authelia config under autonomy.

### Bootstrap admin

Windmill's binary hard-codes the initial admin at:

- **email**: `admin@windmill.dev` (NOT `admin@windmill.local` — chart
  has no admin-email override)
- **password**: `changeme` (default)

The chart does NOT expose `WM_USER_*` env vars. Setting them via
`extraEnv` is silently ignored. The official Windmill bootstrap flow
is: deploy → log in as `admin@windmill.dev` / `changeme` → rotate
password via UI or API.

### Post-deploy: rotate the bootstrap password

The default `changeme` is exposed on the LAN until rotated. The
`windmill` 1P item (vault `Kubernetes`) stores the rotated password
in field `admin_password`. To rotate (or re-rotate after a cluster
rebuild):

```sh
ADMIN_PW=$(op item get "windmill" --vault Kubernetes --reveal \
  --format json | jq -r '.fields[] | select(.label=="admin_password") | .value')

TOKEN=$(curl -sk -X POST https://windmill.${SECRET_DOMAIN}/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@windmill.dev", "password": "changeme"}')

curl -sk -X POST https://windmill.${SECRET_DOMAIN}/api/users/setpassword \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"password\": \"$ADMIN_PW\"}"
```

### 1P item `windmill` (vault `Kubernetes`) — required fields

| Field | What |
|---|---|
| `admin_password` | Rotated admin password (replaces `changeme`). Generate with `openssl rand -base64 32` and store. |
| `encryption_key` | Reserved for future Windmill-side cookie/JWT encryption — not currently consumed. Generate with `openssl rand -hex 32`. |
| `GARAGE_AWS_ACCESS_KEY_ID` | Garage S3 access key for Barman/Postgres backups. From `garage key info windmill-key`. |
| `GARAGE_AWS_SECRET_ACCESS_KEY` | Matching secret. |

Creation recipe (initial deploy):

```sh
ADMIN_PW=$(openssl rand -base64 32)
ENC_KEY=$(openssl rand -hex 32)
op item create \
    --vault Kubernetes \
    --category "Login" \
    --title "windmill" \
    "admin_password[password]=${ADMIN_PW}" \
    "encryption_key[password]=${ENC_KEY}"
# Then provision Garage key/bucket per
# project_garage_cnpg_provisioning_playbook and edit the item to add
# the GARAGE_* fields.
```

### Database

A 3-instance CNPG cluster `postgres-windmill` lives in the `databases`
namespace via the `cnpg-app-database` component.

Two cluster-specific quirks (codified — survive rebuild):

- **`postInitApplicationSQL`** in `bootstrap.initdb` creates the
  `windmill_admin` + `windmill_user` NOLOGIN roles Windmill's first
  migration expects. Without them the app crash-loops with
  `role "windmill_admin" does not exist`.
- **`inheritedMetadata.annotations`** propagates emberstack reflector
  annotations to the `postgres-windmill-app` Secret so the Windmill
  HR (in `home` ns) can consume the DB URI cross-namespace.

### Verify

After Flux reconciles:

```sh
kubectl -n databases get cluster postgres-windmill
kubectl -n home get pod -l release=windmill -o wide
kubectl -n home get httproute windmill -o yaml | grep -i hostname
curl -k https://windmill.${SECRET_DOMAIN}  # → 200 + Windmill UI
```

First image pull is **~5 minutes** — the image is 4 GiB.

## Phase 1b — Authelia SSO via oauth2-proxy (future)

When ready, follow the av1corrector-oauth2-proxy pattern at
`kubernetes/apps/media/av1corrector-oauth2-proxy/` for the manifest
shape, plus register a new OIDC client in Authelia's `OIDC_CLIENTS_YAML`
1P field (`token_endpoint_auth_method: client_secret_post` per memory
`project_authelia_oauth2_proxy_token_endpoint_auth`). Re-point the
HTTPRoute at the oauth2-proxy Service instead of `windmill-app:8000`.

## Phase 2 — Author 7 workflows

Per the migration plan. Workflows checked into git under
`./workflows/<name>.yaml` (Windmill flow format). Tools to use:
`wmill sync` CLI from inside the pod, or the Windmill UI's "Save +
Deploy" + workspace git-sync feature.

Workspace `lovenet` already created via API for this purpose.

Source-of-truth workflows (to be migrated) live at

namespace go away (Phase 3).

## Failure detection

Two layers, deliberately independent:

1. **`windmill-failure-watcher`** (Windmill script, `*/5` cron) — polls the
   jobs API and emails when a script exceeds its failure-rate threshold.
   Rate-driven: catches flapping and sustained breakage.
2. **`windmill-watchdog`** (k8s CronJob, `*/5`, `./app/watchdog-cronjob.yaml`)
   — asserts layer 1 has *succeeded* recently. Presence-of-success, not
   absence-of-failure: if the watcher stops running entirely there are no
   failures for it to report, and nothing else would notice.

Layer 2 lives outside Windmill on purpose. It only reads the jobs API and
needs no Windmill scheduler, dispatch, or error handler to function — so it
stays true when Windmill itself is the thing that broke.

Alerting is `./app/prometheusrule.yaml`, keyed on
`kube_cronjob_status_last_successful_time` (self-clearing) rather than
`kube_job_failed` (which sticks at 1 until the failed Job is GC'd).
`WindmillWatchdogMissing` covers the CronJob itself disappearing.

### The workspace error handler does not work — do not re-add it

Windmill's native **workspace error handler is inert on CE v1.771.0**.
Tested 2026-07-26 with a correct configuration: handler script deployed and
locked, `g/error_handler` group present, `ws_error_handler_muted: false` on
every schedule, setting persisted (tried both `script/f/...` and bare
`f/...` path forms). Result: **5 scheduled failures, 0 handler invocations**,
nothing in the server logs. The workspace setting was reverted to unset — a
handler that claims coverage but never fires is worse than none.

Per-schedule `on_failure` was not tested and may work; it is a different
code path. Verify it actually fires before relying on it.

### Editing the watchdog script

The container image's `date` is **busybox**: `-d "15 minutes ago"` is
rejected, `-d @<epoch>` works. The epoch arithmetic is deliberate.

Do not collapse the curl/jq steps into a single pipeline assignment.
`set -e` does not fire there — the assignment inherits `jq`'s status, and
`jq` succeeds on the empty input a failed `curl` produces. That shape was
verified on 2026-07-26 to report `OK` while the API was unreachable.

Exercise all four paths after any edit (extract the script from the
manifest, mount it via ConfigMap, run it in-namespace):

| case | expected |
|---|---|
| watcher healthy | exit 0 |
| watcher stale / never ran | exit 1 |
| Windmill API unreachable | exit 1 |
| bad token (401) | exit 1 |
