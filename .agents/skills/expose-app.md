---
name: expose-app
description: Attach an app's HTTPRoute to a per-app Gateway listener with shim-managed TLS
---

# Expose App via Per-App TLS Listener

This skill adds a per-app HTTPS listener to one of the cluster Gateways
and points an HTTPRoute at it, so cert-manager's gateway-shim can
auto-provision a dedicated TLS certificate for that hostname.

The migration runbook (`docs/src/per-route-cert-migration.md`) is the
authoritative source for the *why* and the *waves*. This skill is the
mechanical "do one app" workflow used during Phase 2 (canary) and
Phase 3 (waves).

Canonical recent reference: PR #11148 — flux-webhook on `external`.

## When to use

- Adding a brand-new app that should be on per-app TLS from day one.
- Migrating an existing HTTPRoute off the wildcard listener during
  Phase 3 waves.

**Don't use this skill for**:
- Adding the cert-manager annotations to a Gateway (that's Phase 0,
  done once per Gateway).
- istio Gateway routes — they're deferred to Phase 4.0 of the
  runbook because of the reflector dependency.

## Pre-flight checks

Before invoking, confirm:

1. **The Gateway is annotated.** `kubectl get gateway -n network external
   -o jsonpath='{.metadata.annotations}'` must show
   `cert-manager.io/cluster-issuer`. If absent, run Phase 0 first.
2. **cert-manager's gateway-shim is enabled** (`config.enableGatewayAPI:
   true` in the chart values; verify with `kubectl get cm -n cert-manager
   cert-manager -o jsonpath='{.data.config\.yaml}'`).
3. **Wave budget intact.** `kubectl get certificate -A` should show no
   Pending or Failing state. If a recent wave hit a rate-limit error,
   wait before adding more.
4. **The target app can tolerate a brief outage.** The HTTPRoute
   `sectionName` flip leaves the route on `Programmed=False` until
   ACME issues (~30s happy path). There's no fallback to the wildcard.

## Workflow

### Step 1: Collect details

Ask the user for:

1. **App name** — used in the listener name (`https-<app>`) and Secret
   name (`<app>-tls`). e.g. `flux-webhook`, `glance`.
2. **Hostname** — the FQDN the HTTPRoute serves, e.g.
   `flux-webhook.${SECRET_DOMAIN}`. This becomes the listener's
   `hostname` field and the cert's only SAN.
3. **Target Gateway**:
   - `external` (network ns) — Internet-exposed, ACME LE
   - `internal` (network ns) — internal only; uses ACME LE *or*
     `cluster-internal-ca` depending on which issuer the Gateway is
     currently annotated with
   - `mcp-gateway` (mcp-system) — if you're moving an MCP route off
     the wildcard *and* the wildcard listener is gone (TLS
     termination changes; check the runbook before touching)
   - **NOT** `istio` — see runbook Phase 4.0.
4. **HTTPRoute file path** — usually
   `kubernetes/apps/<ns>/<app>/app/helmrelease.yaml` (for app-template
   apps that embed an HTTPRoute via `route.<name>`) or
   `kubernetes/apps/<ns>/<app>/app/httproute.yaml` (standalone).

### Step 2: Add the listener to the target Gateway

Edit the Gateway YAML (one of):

- `kubernetes/apps/network/envoy-gateway/config/external.yaml`
- `kubernetes/apps/network/envoy-gateway/config/internal.yaml`
- `kubernetes/apps/mcp-system/mcp-gateway/app/gateway.yaml`

Append a listener block to `spec.listeners`. Sort listener entries
alphabetically by `name`:

```yaml
    - name: https-<app>
      protocol: HTTPS
      port: 443
      hostname: "<app>.${SECRET_DOMAIN}"      # or whatever the FQDN is
      allowedRoutes:
        namespaces:
          from: All
      tls:
        certificateRefs:
          - kind: Secret
            name: <app>-tls
```

Don't touch the existing `https` (wildcard) listener — it stays as a
backstop for everything else until Phase 4.

### Step 3: Flip the HTTPRoute's sectionName

In the HTTPRoute (or the `route.<name>.parentRefs` block in an
app-template `helmrelease.yaml`):

```diff
   parentRefs:
     - name: <gateway-name>
       namespace: <gateway-namespace>
-      sectionName: https
+      sectionName: https-<app>
```

### Step 4: Commit + push + PR

One PR per app during the canary. Batches of 5 during Phase 3 waves.

Commit message form:
```
feat(network): cert migration canary — <app> on per-app listener
```
or for waves:
```
feat(network): per-app TLS migration wave N (X/Y)
```

The PR body should call out:
- which Gateway is being touched
- the specific app(s)
- a rollback note: revert the PR, the route falls back to whatever
  `sectionName` value it had before

### Step 5: Watch the cert appear

After Flux reconciles (force with `flux reconcile source git
home-ops-kubernetes -n flux-system && flux reconcile kustomization
envoy-gateway-config -n network`), the shim should create a
`<app>-tls` Certificate within seconds of the listener appearing.

```sh
# Watch certs in the Gateway's namespace
kubectl get certificate -n network -w

# Verify the new listener became Programmed
kubectl get gateway -n network <gateway> \
  -o jsonpath='{range .status.listeners[?(@.name=="https-<app>")]}programmed={.conditions[?(@.type=="Programmed")].status}{"\n"}{end}'

# Verify the HTTPRoute is Accepted on the new listener
kubectl get httproute -n <ns> <name> \
  -o jsonpath='{range .status.parents[*]}sectionName={.parentRef.sectionName} accepted={.conditions[?(@.type=="Accepted")].status}{"\n"}{end}'
```

Listener should reach `Programmed=True` and HTTPRoute `Accepted=True`
on `https-<app>` within ~30s of the cert reaching Ready.

### Step 6: Smoke-test

For internal Gateway routes (private CA), curl from inside the cluster
since the hostname won't resolve from outside:

```sh
kubectl run smoke -n <ns> --rm -i --restart=Never \
  --image=curlimages/curl:8.10.1 -- \
  curl -sk -o /dev/null -w 'HTTP %{http_code}\n' \
    https://<hostname>/ --max-time 10 \
    --resolve <hostname>:443:<gateway-LB-IP>
```

For external Gateway routes, hit the public IP directly:

```sh
ext_ip=$(kubectl get svc -n network \
  -l 'gateway.envoyproxy.io/owning-gateway-name=external' \
  -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}')
echo | openssl s_client -connect "${ext_ip}:443" \
  -servername <hostname> </dev/null 2>/dev/null | \
  openssl x509 -noout -subject -ext subjectAltName -dates
```

The cert subject should be `CN=<hostname>` and the SAN should list
*only* that one hostname.

## Gotchas

- **No fallback.** If the new listener fails to become Programmed
  (ACME error, DNS01 misconfig, anything), the HTTPRoute is just
  down. The wildcard listener does NOT serve as a backstop — it only
  catches HTTPRoutes whose `sectionName` explicitly points at it.
  Pick canaries / wave members you can briefly drop.

- **`cert-manager.io/duration` ≠ LE-issued validity.** Let's Encrypt
  always issues 90-day certs from the default profile regardless of
  what cert-manager requests. The `duration: 168h` annotation
  controls only cert-manager's *renewal cadence* (renew at age=5d).
  If you need genuinely short-lived (~6d) certs from LE, opt into
  the `tlsserver` profile via
  `acme.cert-manager.io/order-profile-name: tlsserver` on the
  per-listener Certificate.

- **Stale HTTPRoute status entries.** After flipping `sectionName`,
  Envoy Gateway may show TWO entries in `status.parents` — one for
  the old listener (`Accepted=False`) and one for the new one
  (`Accepted=True`). The stale entry is harmless and clears within
  a few minutes.

- **mcp-gateway has no TLS termination of its own.** It receives
  plaintext HTTP from the public Envoy gateway over Istio. Don't
  add HTTPS listeners to it; the migration for MCP routes happens
  on the *upstream* Gateway that fronts mcp-gateway.

## After it ships — soak

For the canary (Phase 2): soak ≥24h, then watch for the first
auto-renewal at age ≈ 5d to confirm the renewal cycle works. Don't
proceed to Phase 3 waves until both pass.

For waves: 12h soak between waves. Watch
`kubectl describe clusterissuer letsencrypt-production` for any
rate-limit warnings before kicking off the next batch.
