# ntfy

Self-hosted push notification backend. Replaces Pushover in the
Windmill → Pushover → phone path with tap-to-action buttons in the
notification surface.

See `~/.claude-personal/plans/vivid-riding-waterfall.md` for the full
migration rationale + phases.

## Phase 1 — what this PR ships

- HelmRelease (app-template) running `binwiederhier/ntfy:v2.13.0`
- Two `ceph-block` PVCs:
  - `ntfy-cache` (5 GiB, `/var/cache/ntfy`) — message + attachment cache
  - `ntfy-data` (1 GiB, `/var/lib/ntfy`) — auth db + (future) web-push db
- Server config at `/etc/ntfy/server.yml` (from `ntfy-configmap`)
- HTTPRoute on `ntfy.${SECRET_DOMAIN}` attached to **both** the
  external (cloudflared) and internal gateways
- CiliumNetworkPolicy allowing envoy ingress + windmill in-cluster
  ingress + prometheus metrics scrape
- `auth-default-access: deny-all` — no anonymous publish/subscribe

This PR does **not** touch the Windmill workflows. The
Pushover-shape workflows on `feat/windmill-workflows` keep working
in parallel. Workflow edits land in a follow-up PR after this app
is verified.

## Post-deploy bootstrap (one-time)

After Flux reconciles and the pod is `Running`:

### 1. Provision the `ntfy` 1P item

Run on the desktop (cluster 1P Connect token is read-only — see
`reference_1p_connect_token_readonly` memory):

```sh
ADMIN_PW=$(openssl rand -base64 32)
WINDMILL_PW=$(openssl rand -base64 32)
PHONE_PW=$(openssl rand -base64 32)

op item create \
  --vault Kubernetes \
  --category "Login" \
  --title "ntfy" \
  "admin_password[password]=${ADMIN_PW}" \
  "windmill_password[password]=${WINDMILL_PW}" \
  "phone_password[password]=${PHONE_PW}" \
  "URL=https://ntfy.${SECRET_DOMAIN}"
```

### 2. Create users + ACLs inside the pod

```sh
# Fetch passwords back from 1P.
ADMIN_PW=$(op item get ntfy --vault Kubernetes --reveal --format json | \
  jq -r '.fields[] | select(.label=="admin_password") | .value')
WINDMILL_PW=$(op item get ntfy --vault Kubernetes --reveal --format json | \
  jq -r '.fields[] | select(.label=="windmill_password") | .value')
PHONE_PW=$(op item get ntfy --vault Kubernetes --reveal --format json | \
  jq -r '.fields[] | select(.label=="phone_password") | .value')

# `ntfy user add` reads NTFY_PASSWORD from env to skip the TTY prompt.
kubectl -n home exec deploy/ntfy -- env NTFY_PASSWORD="$ADMIN_PW" \
  ntfy user add --role=admin admin
kubectl -n home exec deploy/ntfy -- env NTFY_PASSWORD="$WINDMILL_PW" \
  ntfy user add windmill
kubectl -n home exec deploy/ntfy -- env NTFY_PASSWORD="$PHONE_PW" \
  ntfy user add rob

# Grant ACLs. windmill = write, rob = read on the four operational topics.
for t in approvals alerts costs digests; do
  kubectl -n home exec deploy/ntfy -- ntfy access windmill "$t" rw
  kubectl -n home exec deploy/ntfy -- ntfy access rob "$t" ro
done

# Verify.
kubectl -n home exec deploy/ntfy -- ntfy user list
kubectl -n home exec deploy/ntfy -- ntfy access
```

### 3. Verify from outside the cluster

```sh
# Anonymous publish must fail (auth-default-access: deny-all).
curl -sS -o /dev/null -w "%{http_code}\n" -d "anon test" \
  https://ntfy.${SECRET_DOMAIN}/approvals  # → 401

# Authenticated publish from windmill role must succeed.
curl -sS -u "windmill:${WINDMILL_PW}" -H "Title: phase-1-test" \
  -H "Actions: http, Test action, https://httpbin.org/post" \
  -d "tap the action button" \
  https://ntfy.${SECRET_DOMAIN}/approvals
```

### 4. Subscribe on the Android phone

1. Install ntfy from F-Droid: `io.heckel.ntfy`
2. Settings → Default server: `https://ntfy.${SECRET_DOMAIN}`
3. Settings → Manage users → add server + `rob` credentials
4. Subscribe to topics: `approvals`, `alerts`, `costs`, `digests`
5. Per-topic notification settings:
   - `approvals` — high priority, override DND, distinct sound
   - `alerts` — high priority
   - `costs`, `digests` — default

### 5. End-to-end action button test

Re-run the curl from step 3 with `windmill` creds. Phone should
receive the push with a "Test action" button. Tapping it should
POST to `httpbin.org/post`. Confirm via Android app activity feed
that the action succeeded.

## Phase 1 verification gate

Mark Phase 1 complete when:

- [ ] All four topics receive an authenticated test message
- [ ] Phone receives the push within 5 seconds on cellular
- [ ] Phone receives the push within 5 seconds on home WiFi
- [ ] Tapping the action button hits the target URL
- [ ] Anonymous publish returns 401
- [ ] Prometheus is scraping `ntfy:9090/metrics` (check
      `up{job="serviceMonitor/home/ntfy/0"}` in Prometheus)

Once green, kick off Phase 2 (workflow file edits in
`feat/windmill-workflows-ntfy` or wherever the next branch lands).

## Phase 1 follow-ups (separate PR or merged into Phase 2)

- Enable web push (VAPID keys, externalsecret) for the laptop PWA path
- Pin chart digest after first stable upgrade
- Route the container image pull through ZOT if/when container
  pull-through caching lands (today ZOT only proxies Helm charts)
