# Per-Route Cert Migration Runbook

This runbook covers migrating from one shared wildcard ACME certificate
(`thesteamedcrab.com` covering `*.thesteamedcrab.com`) to per-listener
fine-grained short-term certificates, using cert-manager's Gateway API
shim to auto-provision a Certificate per HTTPRoute hostname.

**Why**: smaller blast radius per renewal failure, per-host visibility,
no reflector dependency for new namespaces, faster rotation.

**Why this needs to be paced**: Let's Encrypt limits issuance to 50
certificates per Registered Domain per rolling 7-day window. The
cluster has ~70 HTTPRoutes under `thesteamedcrab.com` — too many to
flag-day onto ACME without breaching the limit and locking out *all*
issuance for ~7 days.

## State at the start

| Aspect | Reality |
|--------|---------|
| Cert manifest | One `Certificate` (`thesteamedcrab.com`) producing `thesteamedcrab-com-tls` |
| Cert SANs | apex, `*.thesteamedcrab.com`, `*.app.…`, `*.mcp.…` |
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

## Phase 0 — Pre-flight verification

The plan rests on cert-manager's gateway-shim being a no-op for
listeners whose `certificateRefs` Secret is already managed by an
existing manual Certificate. **This needs to be verified, not
assumed.** Historically the shim has tracked Certificates by
ownerReference (Gateway → Certificate), not globally by Secret name —
so an existing manual `Certificate` without a Gateway ownerRef may not
satisfy the shim's check, and adding `cert-manager.io/cluster-issuer`
to the Gateway could cause the shim to create a *second* Certificate
fighting for the same Secret.

### 0.1 Test in a scratch namespace

Before touching production Gateways:

```sh
kubectl create ns cert-shim-test

# A throwaway manual Certificate for foo.scratch.thesteamedcrab.com
# (use letsencrypt-staging — no rate-limit cost)
cat <<EOF | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: scratch
  namespace: cert-shim-test
spec:
  secretName: scratch-tls
  issuerRef:
    name: letsencrypt-staging
    kind: ClusterIssuer
  dnsNames: [foo.scratch.thesteamedcrab.com]
EOF

# Wait until Ready
kubectl wait --for=condition=Ready certificate/scratch \
  -n cert-shim-test --timeout=5m

# Throwaway Gateway referencing that Secret + the shim annotation
cat <<EOF | kubectl apply -f -
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: scratch
  namespace: cert-shim-test
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-staging
spec:
  gatewayClassName: envoy
  listeners:
    - name: https
      protocol: HTTPS
      port: 443
      hostname: foo.scratch.thesteamedcrab.com
      allowedRoutes:
        namespaces: { from: Same }
      tls:
        certificateRefs:
          - kind: Secret
            name: scratch-tls
EOF

# Watch the namespace's Certificates for ~2 min
kubectl get certificate -n cert-shim-test -w
```

**Decision tree**:
- ✅ Only the original `scratch` Certificate appears, no shim-created
  duplicate → shim is Secret-aware. Phase 0 in production is safe;
  proceed.
- ❌ A second Certificate (named like `scratch-https` or
  `<gateway>-<listener>`) appears → shim is ownerRef-only. Adopt the
  alternative Phase 0 below.

### 0.2 Cleanup

```sh
kubectl delete ns cert-shim-test
```

### 0.3 If the shim made a duplicate, adjust strategy

Phase 0 (the bulk annotation add) is unsafe. Skip it. Instead:

- Don't add `cert-manager.io/cluster-issuer` to the Gateway upfront.
- For each per-app listener you add (Phase 1+), add the annotation
  *and* the listener in the same commit. The annotation now applies
  but the only listener with a "missing Secret" is the new one — so
  the shim creates exactly one Certificate (for the new app).
- The existing wildcard listener still has a managed Secret. If the
  shim creates a duplicate Certificate against it the moment the
  annotation is present, you have to either: (a) accept the duplicate
  and immediately delete one of the two Certificates, then watch
  whether the shim re-creates it, or (b) precede each annotation add
  by removing the wildcard listener from that Gateway and serving its
  routes via per-app listeners only.
- This path is sufficiently fiddly that **if the test in 0.1 fails,
  stop and revisit the architecture.** Don't improvise in
  production.

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
  commonName: thesteamedcrab.com Internal CA
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
at least your primary device. (Open `https://glance.thesteamedcrab.com`
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

If Phase 0.1 verified the shim is Secret-aware, add the annotation to
the Gateway in this same commit:

```yaml
metadata:
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-production
    cert-manager.io/duration: "168h"        # 7d
    cert-manager.io/renew-before: "48h"
```

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
echo | openssl s_client -connect <app>.thesteamedcrab.com:443 \
  -servername <app>.thesteamedcrab.com 2>/dev/null \
  | openssl x509 -noout -subject -ext subjectAltName -dates
```

Soak 24h. If anything is wrong, **revert the HTTPRoute sectionName**.
The wildcard listener still serves the old wildcard cert; no user
impact.

Don't proceed past Phase 2 until this canary has cleanly soaked AND
auto-renewed at least once (`renewBefore: 48h` means renewal kicks in
on day 5 — wait for that).

## Phase 3 — Wave migration on public Gateways

Goal: migrate the remaining ~21 public-facing HTTPRoutes (10 on
external, 9 on mcp-gateway, 2 on istio) at **5 per day** with 12h
soaks.

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

Smallest count first to surface issues with smaller blast radius:

1. Wave 1: `istio` (2 routes — fold into wave 2 if room)
2. Wave 2: `external` (5 of 10, the canary already done)
3. Wave 3: `external` (the other 5 + remainder)
4. Wave 4: `mcp-gateway` (5 of 9)
5. Wave 5: `mcp-gateway` (the other 4)

Total ~5 calendar days at one wave per ~12h.

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

### 4.1 Verify no consumers reference the wildcard Secret

```sh
grep -rn 'thesteamedcrab-com-tls\|${SECRET_DOMAIN/./-}-tls' kubernetes/
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
for host in glance.thesteamedcrab.com photos.thesteamedcrab.com ... ; do
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
- ACME audit log at <https://crt.sh/?Identity=thesteamedcrab.com> —
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
  (<https://crt.sh/?Identity=thesteamedcrab.com>) — anyone can
  enumerate your service catalog from the cert transparency feed.
  Mitigate with hostnames that don't betray service identity if this
  matters.
