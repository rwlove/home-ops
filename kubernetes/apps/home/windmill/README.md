# Windmill

FOSS workflow automation tool, replacing n8n. See the migration plan in
`docs/src/audit/` and the steady-marshmallow plan at
`~/.claude-personal/plans/i-want-you-to-steady-marshmallow.md`.

## Activation (Phase 1a — no SSO yet)

This is the initial deploy with Windmill's native admin auth. Authelia
OIDC SSO via oauth2-proxy is deferred to Phase 1b (separate PR) to
avoid editing the cluster's 24-OIDC-client Authelia config under
autonomy.

### 1P item `windmill` (vault `Kubernetes`) — required fields

| Field | What |
|---|---|
| `admin_password` | Initial admin password for `WM_USER_USERNAME=admin`. Generate with `openssl rand -base64 32` and store. |
| `encryption_key` | Reserved for future Windmill-side cookie/JWT encryption — not currently consumed by this manifest. Generate with `openssl rand -hex 32` and store. |

Creation recipe:

```sh
ADMIN_PW=$(openssl rand -base64 32)
ENC_KEY=$(openssl rand -hex 32)
op item create \
    --vault Kubernetes \
    --category "Login" \
    --title "windmill" \
    "admin_password[password]=${ADMIN_PW}" \
    "encryption_key[password]=${ENC_KEY}"
```

### Database

A 3-instance CNPG cluster `postgres-windmill` is provisioned in the
`databases` namespace via the cnpg-app-database component. Connection
parameters land in the `postgres-windmill-app` Secret (created by CNPG)
with a ready-to-use `uri` field. The Windmill HR's
`windmill.databaseUrlSecretName` + `databaseUrlSecretKey` point at that
secret + the `uri` field.

### Verify

After Flux reconciles:

```sh
kubectl -n databases get cluster postgres-windmill
kubectl -n home get pod -l app.kubernetes.io/name=windmill -o wide
kubectl -n home get httproute windmill -o yaml | grep -i hostname
curl -k https://windmill.${SECRET_DOMAIN}  # → 200 + Windmill UI
```

Log in via the UI with `admin@windmill.local` + the password from the
1P item.

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
`windmill sync` CLI from inside the pod, or the Windmill UI's "Save +
Deploy" + git-sync feature.
