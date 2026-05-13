# Per-Route Cert Migration Runbook

This runbook covers migrating from one shared wildcard ACME certificate
(`${SECRET_DOMAIN}` covering `*.${SECRET_DOMAIN}`) to per-listener
fine-grained short-term certificates, using cert-manager's Gateway API
shim to auto-provision a Certificate per HTTPRoute hostname.

**Why**: smaller blast radius per renewal failure, per-host visibility,
no reflector dependency for new namespaces, faster rotation.

**Why this needs to be paced**: Let's Encrypt limits issuance to 50
certificates per Registered Domain per rolling 7-day window. The
cluster has ~70 HTTPRoutes under `${SECRET_DOMAIN}` — too many to
flag-day onto ACME without breaching the limit and locking out *all*
issuance for ~7 days.

## State at the start

| Aspect | Reality |
|--------|---------|
| Cert manifest | One `Certificate` (`${SECRET_DOMAIN}`) producing `${SECRET_DOMAIN/./-}-tls` |
| Cert SANs | apex, `*.${SECRET_DOMAIN}`, `*.app.…`, `*.mcp.…` |
| Reflection | Secret reflected to `network`, `istio-system`, `mcp-system` |
| Issuer | `letsencrypt-production` (DNS01 via Cloudflare) |
| Gateways | 4 — `external` (network), `internal` (network), `istio` (istio-system), `mcp-gateway` (mcp-system) |
| HTTPRoutes per Gateway | external: 11, internal: 50+, mcp-gateway: 9, istio: 2 |
| Existing TTL | LE default 90d, autorenew at ~30d remaining |

## Target state — split by exposure

The 70+ routes split into two cohorts:

- **Public-facing** (~22): `external` (11), `mcp-gateway` (9), `istio` (2).
  These need browser-trusted certs → **ACME via `letsencrypt-production`**.
- **Internal-only** (~50): everything on the `internal` Gateway. These
  never face the public internet → **private CA**, issued by an
  in-cluster ClusterIssuer. Zero rate-limit concern, instant
  issuance, no public CT log entries leaking internal hostnames.

### Why split

A "just ACME everything" plan would burn the entire 50/week budget
twice over and leave no headroom for retries or the existing
wildcard's renewal. Splitting halves the scope of the rate-limited
work and removes the public CT log noise for internal services.

The cost of the split is **one-time**: distribute the private CA root
to every device that visits internal hostnames (browsers, mobile
devices, anything that calls internal APIs). That's a manual import on
each device.

If that operational cost is unacceptable, see "Alternative: all-ACME"
at the bottom of this runbook.

## Rate-limit math (ACME side, ~22 certs)

- **Hard ceiling**: 50 issuances / 7d / Registered Domain
- **Total ACME target certs**: ~22
- **Renewal exemption**: renewals (same FQDN set, same account) are
  exempt from the 50/week ceiling but still subject to a 5/week
  Duplicate Certificate cap *per FQDN set*. With `duration: 168h` /
  `renewBefore: 48h`, each cert renews ~1.4×/week — well under 5/week.
- **Sustainable initial-issuance rate**: `50 ÷ 7 ≈ 7/day`. The 7-day
  window is rolling, not calendar — at 10/day you hit the cap on day
  5, not day 7.
- **Wave size**: 5/day. Buffer of 2/day for retries and the existing
  wildcard's renewal pressure.
- **Total elapsed**: ~5 days for 22 certs.

## Critical gotchas (learned the hard way)

Three things bit us during the first execution attempt on 2026-05-02.
Read these *before* writing any YAML.

### 1. The gateway-shim must be explicitly enabled

cert-manager v1.16+ does **not** enable the gateway-shim by default,
contrary to what some release notes suggest. You need
`config.enableGatewayAPI: true` in the Helm values. Without it,
annotating a Gateway with `cert-manager.io/cluster-issuer` is **silently
inert** — no Certificate is ever created. Verify before Phase 0:

```sh
kubectl get cm -n cert-manager cert-manager -o jsonpath='{.data.config\.yaml}'
# Expect to see: enableGatewayAPI: true
```

If absent, set it in the chart values and roll out cert-manager *first*.

### 2. The reflector pattern fights the gateway-shim across namespaces

This cluster mirrors `network/${SECRET_DOMAIN/./-}-tls` to `istio-system`
and `mcp-system` via reflector. The gateway-shim is **per-namespace**:
it creates a Certificate in the same namespace as the Gateway. So
annotating the istio Gateway whose listener references the
reflector-mirrored Secret causes:

1. shim creates a `Certificate` in `istio-system` targeting the
   mirrored Secret's name
2. cert-manager re-issues from ACME because "Secret was previously
   issued by a different issuer" (1 ACME order against the budget)
3. ongoing fight: shim's Certificate vs reflector for ownership of
   the Secret

**Don't annotate any Gateway whose listener still references a
reflector-mirrored Secret.** Migrate that Gateway's HTTPRoutes to
per-app listeners with their own Secret names *first*, then remove
the wildcard listener and the reflector dependency, then add the
shim annotation.

For this cluster: the istio Gateway is excluded from Phase 0. It gets
annotated only after its (small) HTTPRoute set is migrated and the
wildcard listener is removed.

### 3. HTTPRoute `sectionName` has no fallback

A HTTPRoute attached to a listener that goes `Programmed=False`
**does not fall back to other listeners** that match its hostname.
The route is just down. The wildcard listener you keep around
"as a backstop" only catches HTTPRoutes that explicitly point at it
via `sectionName`.

**Implication for the canary**: don't pick an app whose downtime is
unacceptable. The canary's listener is `Programmed=False` until ACME
issues, which can be ~30s but can be much longer if anything's wrong.

## Phase 0 — Annotation prep

Verified 2026-05-02 against cert-manager v1.17.1 with
`config.enableGatewayAPI: true`: when the shim sees a listener whose
referenced Secret doesn't exist (or isn't owned by an in-namespace
Certificate), it creates one. When an in-namespace Certificate already
owns the Secret, the shim is a no-op.

For this cluster, that means it's **safe** to annotate Gateways in the
`network` namespace (which holds the manual `${SECRET_DOMAIN}`
Certificate that owns `${SECRET_DOMAIN/./-}-tls`), and **unsafe** to
annotate Gateways in `istio-system` or `mcp-system` while they still
reference the reflector-mirrored Secret.

### 0.1 Pre-flight test (one-time)

Confirm the shim creates Certificates for new Secret refs:

```sh
kubectl apply -f - <<'EOF'
---
apiVersion: v1
kind: Namespace
metadata: { name: shim-test }
---
apiVersion: cert-manager.io/v1
kind: Issuer
metadata: { name: ss, namespace: shim-test }
spec: { selfSigned: {} }
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: scratch
  namespace: shim-test
  annotations:
    cert-manager.io/issuer: ss
spec:
  gatewayClassName: envoy
  listeners:
    - name: https
      protocol: HTTPS
      port: 443
      hostname: bar.shim-test.${SECRET_DOMAIN}
      allowedRoutes: { namespaces: { from: Same } }
      tls:
        certificateRefs: [{ kind: Secret, name: bar-shim-tls }]
EOF
sleep 20
kubectl get certificate -n shim-test
kubectl delete namespace shim-test
```

Expected: a `bar-shim-tls` Certificate appears, `Ready: True`. If
nothing appears within 30s, the shim isn't running — fix that before
proceeding.

### 0.2 Add the annotations

Annotate **only** the `external` and `internal` Gateways
(`network` namespace). Skip `istio` and `mcp-gateway` for now —
they're in namespaces that depend on the reflector. They'll be
annotated in Phase 5 after their HTTPRoutes have moved off the
shared Secret.

```yaml
metadata:
  annotations:
    # ...existing annotations...
    cert-manager.io/cluster-issuer: letsencrypt-production
    cert-manager.io/duration: "168h"      # 7d
    cert-manager.io/renew-before: "48h"
```

> **What `duration` actually does.** Let's Encrypt's default profile
> always issues 90-day certificates regardless of the `duration`
> requested by ACME. The `cert-manager.io/duration` annotation controls
> only cert-manager's *renewal cadence* — it tells cert-manager "treat
> this cert as expiring after 168h" so it renews early. You still get
> 90-day certs, just rotated every ~5 days.
>
> For actually-short LE certs, opt into the `tlsserver` profile (~6-day
> validity) by adding `acme.cert-manager.io/order-profile-name:
> tlsserver` on the per-listener Certificate or via cert-manager's
> issuer-level configuration. That changes the LE order profile and
> the issued cert is genuinely short-lived. Verify with
> `openssl s_client … | openssl x509 -noout -dates` after issuance.

For the `internal` Gateway, swap the issuer to the private CA once
Phase 1 is done:

```yaml
    cert-manager.io/cluster-issuer: cluster-internal-ca
```

### 0.3 Verify

```sh
# Cert count must be unchanged after reconcile.
kubectl get certificate -A

# If a new Certificate appears in any namespace, the shim hit
# something it shouldn't have. Stop and investigate.
```

If a Certificate appeared, check whether it's in a namespace whose
Gateway you annotated. If yes, you almost certainly hit case (2) above
— the listener references a reflector-mirrored Secret. Revert the
annotation and migrate that Gateway separately later.

## Phase 1 — Internal Gateway → private CA (parallel track)

This phase is independent of the ACME phases below — it has no rate
limit concerns and can run in any order or in parallel.

### 1.1 Set up the private CA

(One-time. Skip if already present.)

```yaml
# cert-manager: bootstrap a self-signed root + ClusterIssuer
---
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: selfsigned-bootstrap
spec:
  selfSigned: {}
---
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: cluster-internal-ca
  namespace: cert-manager
spec:
  isCA: true
  commonName: ${SECRET_DOMAIN} Internal CA
  secretName: cluster-internal-ca-tls
  duration: 87600h  # 10y root
  renewBefore: 720h
  privateKey:
    algorithm: ECDSA
    size: 256
  issuerRef:
    name: selfsigned-bootstrap
    kind: ClusterIssuer
---
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: cluster-internal-ca
spec:
  ca:
    secretName: cluster-internal-ca-tls
```

### 1.2 Distribute the root to client devices

This is the non-zero operational cost. Export the CA cert and import
it into:
- Each laptop / desktop browser (or system trust store)
- Each phone / tablet
- Any in-cluster client that calls internal hostnames over TLS

```sh
kubectl get secret -n cert-manager cluster-internal-ca-tls \
  -o jsonpath='{.data.tls\.crt}' | base64 -d > internal-ca.crt
```

Don't proceed past 1.2 until you've imported and verified the root on
at least your primary device. (Open `https://glance.${SECRET_DOMAIN}`
post-migration and confirm no cert warning.)

### 1.3 Migrate the internal Gateway

Add `cert-manager.io/cluster-issuer: cluster-internal-ca` annotation
to `kubernetes/apps/network/envoy-gateway/config/internal.yaml`. Then,
in batches of 10 for review-ability, add a per-app listener and flip
the corresponding HTTPRoute's `sectionName`. Issuance is instant —
no soak required between batches; just verify each Certificate goes
Ready before moving on.

The wildcard listener stays alive until every internal HTTPRoute is
migrated, so rollback is the same as the ACME track: revert
`sectionName` and the route falls back to the wildcard.

## Phase 2 — Public Gateways canary (1 app)

Pick a low-stakes route on the **external** Gateway (the highest
public visibility). Avoid the highest-traffic apps (immich, jellyfin)
until after canary; pick something like the github webhook or a
seldom-used bookmark.

> ⚠ **No fallback.** Once you flip the HTTPRoute's `sectionName` to
> the new per-app listener, the route is bound to that listener
> *only*. If ACME issuance fails or is slow, the route returns 404 (or
> connection refused) until the cert lands — the wildcard listener
> does **not** serve as a backstop. Pick a canary you can afford to
> have down for ~30s in the happy case and several minutes in the
> failure case.

### 2.1 Add a per-app listener

```yaml
spec:
  listeners:
    # existing http and wildcard https listeners untouched
    - name: https-<app>
      protocol: HTTPS
      port: 443
      hostname: "<app>.${SECRET_DOMAIN}"
      allowedRoutes:
        namespaces:
          from: All
      tls:
        certificateRefs:
          - kind: Secret
            name: <app>-tls
```

The Gateway already carries the shim annotations from Phase 0; this
new listener picks them up automatically. cert-manager will create a
`<app>-tls` Certificate within seconds of the listener appearing.

### 2.2 Flip the HTTPRoute's sectionName

```diff
   parentRefs:
     - name: external
       namespace: network
-      sectionName: https
+      sectionName: https-<app>
```

### 2.3 Verify and soak

```sh
# Cert issues in seconds for staging, ~30s for production
kubectl get certificate -n network -w

# Confirm the SAN is just the one hostname and duration ≈ 7d
echo | openssl s_client -connect <app>.${SECRET_DOMAIN}:443 \
  -servername <app>.${SECRET_DOMAIN} 2>/dev/null \
  | openssl x509 -noout -subject -ext subjectAltName -dates
```

Soak 24h. If anything is wrong, **revert the HTTPRoute sectionName**.
The wildcard listener still serves the old wildcard cert; no user
impact.

Don't proceed past Phase 2 until this canary has cleanly soaked AND
auto-renewed at least once (`renewBefore: 48h` means renewal kicks in
on day 5 — wait for that).

## Phase 3 — Wave migration on public Gateways

Goal: migrate the remaining ~19 public-facing HTTPRoutes on Gateways
that are already annotated (`external` 10, `mcp-gateway` 9) at
**5 per day** with 12h soaks. The 2 istio routes are deferred to
Phase 5.

### Per-wave procedure

For each batch of 5:

1. Add 5 listeners to the appropriate Gateway. Sort listeners
   alphabetically inside the `listeners:` block.
2. Flip 5 HTTPRoute `sectionName`s.
3. Commit + push. One PR per wave. Title: `feat(network): per-app TLS
   migration wave N (X/Y)`.
4. Watch issuance:
   ```sh
   kubectl get certificate -A -w
   ```
   All 5 should reach Ready within ~2 min. Anything stuck → check
   `kubectl describe certificate <name> -n network` and the Order /
   Challenge resources.
5. Soak ~12h between waves. Watch the issuer for rate-limit warnings:
   ```sh
   kubectl describe clusterissuer letsencrypt-production
   kubectl get challenge -A
   ```

### Recommended wave order

Public Gateway with the manual `Certificate` first (lower risk —
that's where Phase 0 was verified safe):

1. Wave 1: `external` (5 of 10; canary already done)
2. Wave 2: `external` (the other 5)
3. Wave 3: `mcp-gateway` (5 of 9)
4. Wave 4: `mcp-gateway` (the other 4)

Total ~4 calendar days at one wave per ~12h. The 2 istio routes are
handled in Phase 5 once the reflector dependency is removed.

### What to do if you hit a rate-limit error

cert-manager surfaces LE rate-limit responses in the Order resource:

```sh
kubectl get order -A | grep -i rate
kubectl describe order -n network <order>
```

If hit:
- **Stop the next wave immediately.**
- **Don't delete failing Certificates** — that doesn't reset the
  counter and can cause cert-manager to retry, eating more budget.
- The window is rolling; just wait. The first issuance from N days
  ago drops off after 7d.
- Resume waves once `kubectl get certificate -A` shows no Pending or
  Failing.

## Phase 4 — Cleanup

Run only after every HTTPRoute on every Gateway has been migrated
and soaked for 7+ days.

### 4.0 istio Gateway migration (prerequisite)

The istio Gateway was excluded from Phase 0 / Phase 3 because its
listener references the reflector-mirrored Secret. Migrate its
HTTPRoutes (~2: kiali and one redirect) *before* deleting the
wildcard Certificate.

For each istio HTTPRoute (`<app>` = e.g. `kiali`):

1. Create a manual Certificate in `istio-system` (no shim
   involvement — the istio Gateway is still un-annotated):

   ```yaml
   apiVersion: cert-manager.io/v1
   kind: Certificate
   metadata:
     name: <app>
     namespace: istio-system
   spec:
     secretName: <app>-tls
     issuerRef:
       name: letsencrypt-production
       kind: ClusterIssuer
     dnsNames:
       - <app>.${SECRET_DOMAIN}
     duration: 168h
     renewBefore: 48h
   ```

2. Wait for it to be Ready (1 ACME issuance per cert).

3. Add a per-app listener to the istio Gateway referencing
   `<app>-tls`, then flip the HTTPRoute's `sectionName`.

4. Soak briefly. Confirm the HTTPRoute serves via the new listener.

After all istio HTTPRoutes are migrated:

5. Remove the wildcard listener from the istio Gateway.

6. Remove `istio-system` from the wildcard Certificate's
   `reflector...reflection-allowed-namespaces` annotation list (in
   `kubernetes/apps/network/envoy-gateway/config/certificate.yaml`).
   This stops new mirroring; existing Secret in istio-system can be
   deleted manually if desired (no consumers).

7. Now the istio Gateway has no reflector-mirrored Secret. Add the
   `cert-manager.io/cluster-issuer` annotations from Phase 0. The
   shim is now safe — every listener's referenced Secret is owned by
   an in-namespace manual Certificate. (Optionally, delete the manual
   Certificates afterward and let the shim recreate them, at the
   cost of one ACME order per cert.)

### 4.1 Verify no consumers reference the wildcard Secret

```sh
grep -rn '${SECRET_DOMAIN/./-}-tls\|${SECRET_DOMAIN/./-}-tls' kubernetes/
```

Anything still referencing the wildcard Secret needs to migrate first.
Particular attention: Gateways in `istio-system` and `mcp-system` may
still be using the reflector-mirrored copy.

### 4.2 Remove the wildcard listener from each Gateway

Delete the `name: https` block (with `hostname: "*.${SECRET_DOMAIN}"`)
from each of:

- `kubernetes/apps/network/envoy-gateway/config/external.yaml`
- `kubernetes/apps/network/envoy-gateway/config/internal.yaml`
- `kubernetes/apps/istio-system/gateway/gateway.yaml`
- `kubernetes/apps/mcp-system/mcp-gateway/app/gateway.yaml`

### 4.3 Delete the wildcard Certificate manifest

```sh
git rm kubernetes/apps/network/envoy-gateway/config/certificate.yaml
```

Also remove the entry from
`kubernetes/apps/network/envoy-gateway/config/kustomization.yaml`.

The reflector annotations on the (now-deleted) Certificate's
`secretTemplate` go away automatically; reflector stops mirroring;
the reflected Secret copies in `istio-system` and `mcp-system` are
garbage-collected.

### 4.4 Final verify

```sh
# No wildcard cert in any namespace
kubectl get certificate -A | grep -i wildcard

# Per-app certs all present and Ready
kubectl get certificate -A

# All public certs are short-duration; private CA certs may be longer
kubectl get certificate -A -o json | \
  jq -r '.items[] | "\(.metadata.namespace)/\(.metadata.name) duration=\(.spec.duration // "unset")"'

# Smoke test from outside (public) and from a CA-trusting device (internal)
for host in glance.${SECRET_DOMAIN} photos.${SECRET_DOMAIN} ... ; do
  echo "$host:"
  echo | openssl s_client -connect "$host:443" -servername "$host" 2>/dev/null \
    | openssl x509 -noout -subject -dates
done
```

## Rollback

The wildcard cert + listeners stay alive through Phase 1, 2, and 3.
Rollback during those phases is just reverting the HTTPRoute's
`sectionName`. Even after deleting some per-app listeners, the
wildcard listener catches the route.

Phase 4 is irreversible by `git revert` alone — once the wildcard
Certificate is deleted, re-creating it kicks off a new ACME order
(counts against rate limit). If you must roll back from Phase 4:

1. Restore the Certificate manifest via git revert
2. cert-manager re-issues — costs 1 against the 50/week budget
3. Restore the wildcard listeners
4. Flip HTTPRoutes back to `sectionName: https`

So don't enter Phase 4 unless you're confident in the new state.

## What to watch

- `kubectl get certificate -A` — column `READY` should always be `True`
- `kubectl get order -A` — should be empty in steady state (orders
  exist transiently during issuance/renewal)
- `kubectl describe clusterissuer letsencrypt-production` — surfaces
  ACME backoff messages if rate-limited
- ACME audit log at <https://crt.sh/?Identity=${SECRET_DOMAIN}> —
  external counter you don't control, useful sanity check

## When to stop and ask

- Any wave produces >0 failed Certificates after 5 min
- Total Pending+Failing Certificates count >3 across the cluster
- Any rate-limit error in Order events
- More than one wave in the rolling 7d window has produced retries
- Phase 0.1 test result was ambiguous

## Alternative: all-ACME (no private CA)

If managing a private CA root on every client device is unacceptable:

- Skip Phase 1 entirely
- All ~70 routes go through ACME
- Wave size still 5/day; total elapsed ~14 days
- Internal hostnames will appear in public CT logs
  (<https://crt.sh/?Identity=${SECRET_DOMAIN}>) — anyone can
  enumerate your service catalog from the cert transparency feed.
  Mitigate with hostnames that don't betray service identity if this
  matters.
