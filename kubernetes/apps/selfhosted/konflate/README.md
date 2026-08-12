# konflate

[konflate](https://github.com/home-operations/konflate) renders this repo's
open PRs at their merge-base vs head and shows the **rendered** Flux diff
(HelmRelease / Kustomization / OCIRepository) in a review UI — image changes,
blast radius, render failures, danger-lint. Internal-only, behind Authelia SSO
at `konflate.${SECRET_DOMAIN}`. Read-only toward GitHub (no PR comments / status
write-back — the `flux-local` CI job already posts the diff comment).

## Topology

```text
envoy (internal gw) ──HTTPRoute──▶ konflate-oauth2-proxy:4180 ──▶ konflate:8080
                                          │
                                          └─OIDC─▶ Authelia (auth.${SECRET_DOMAIN})
konflate ──renders──▶ ZOT (kube-system/zot:5000) + api.github.com + chart upstreams
```

- `konflate/` — the chart (OCIRepository via ZOT + HelmRelease), its ClusterIP
  Service, the HTTPRoute, a `ceph-block` PVC for caches/git-mirror/rendered
  diffs, and the render-egress CNP.
- `konflate-oauth2-proxy/` — oauth2-proxy fronting konflate (mirrors
  `observability/goldilocks-oauth2-proxy`).

## GitHub auth

konflate authenticates to GitHub **as the App itself**, using the same App
(`2931213`) renovate-operator uses. `config.appClientId` is set in the
HelmRelease and the PEM arrives as `KONFLATE_APP_PRIVATE_KEY` from the
1Password `Github` item (`renovate_app_private_key`) — see
`app/externalsecret.yaml`. No standing PAT.

konflate mints its own installation tokens from that key and re-mints them a
minute before each expires, so **no credential has to reach the running
process after startup**. The installation is auto-detected from the repo, so
no installation id is configured. A configured App is konflate's identity for
reads as well as writes (`newGitHubReadClient` precedence: App →
`KONFLATE_TOKEN` → anonymous), which is why no token is supplied at all.

konflate stays read-only (`prComments`/`statusChecks` off), so the App's write
scope is unused; upstream AND-gates both features on those flags
(`StatusChecksEnabled` / `PRCommentsEnabled`), so supplying the PEM does not
by itself enable write-back.

### Why not the installation token

The original design minted the installation token *outside* the pod, via a
`GithubAccessToken` generator. That token expires after 1 hour, and the chart
delivers `secret.existingSecret` through `envFrom` — a pod's environment is
fixed at start, so konflate could never see a rotated value. It authenticated
for one hour after each start and then 401'd for 3.7 days from 2026-08-08,
while ESO rotated the Secret 182 times into a void.

Nothing caught it: probes are process-level and kept passing, the pod never
restarted, and ESO reported `Ready=True` because minting the token had in
fact succeeded. Only konflate's own counters showed it — 148 of 150 diff jobs
erroring. `app/prometheusrule.yaml` now alerts on them
(`konflate_forge_list_errors_total`, `konflate_diff_jobs_total{result="error"}`),
plus an `absent()` guard so that pair cannot go quiet by vanishing.

`deploymentAnnotations` still carries `reloader.stakater.com/auto: "true"`,
now as a safety net rather than the fix: the PEM does not expire, so it fires
approximately never, but a manual key rotation still needs a pod restart to
take effect.

renovate-operator shares the same App and never hit any of this — it runs a
Job per cycle, so each run starts a fresh pod that reads the current Secret.

renovate-operator shares the same App and never hit this: it runs a Job per
cycle, so each run starts a fresh pod that reads the current Secret.

## Provisioning (done)

The out-of-Git secrets and the Authelia client were created on 2026-06-24:

- **1Password `konflate-oauth2-proxy`** (Kubernetes vault) — the
  `OAUTH_CLIENT_SECRET` and `COOKIE_SECRET` fields, consumed by the
  oauth2-proxy ExternalSecret.
- **Authelia OIDC client `konflate-oauth2-proxy`** — added to the `authelia`
  1Password item's `OIDC_CLIENTS_YAML` (a clone of the `goldilocks-oauth2-proxy`
  client: `admin_only`, PKCE/S256, `client_secret_post`, redirect
  `https://konflate.${SECRET_DOMAIN}/oauth2/callback`). Validated with
  `authelia config validate` (zero new errors vs baseline) before propagation;
  Authelia rolled cleanly.

No further manual 1Password steps are needed to deploy this app.

## Post-deploy validation

- **Egress label match** — the render CNP selects pods labelled
  `app.kubernetes.io/name: konflate`. If konflate logs show source-fetch /
  GitHub timeouts, confirm the chart's pod label and check Hubble for
  `Policy denied DROPPED` egress, then adjust `app/cnp-allow.yaml`.
- **existingSecret keys** — the chart's `secret.existingSecret` documents keys
  `KONFLATE_TOKEN / KONFLATE_WEBHOOK_SECRET / ...`. Only `KONFLATE_TOKEN` is
  provided (read-only mode). If the pod fails on a missing key, add the empty
  key(s) to the ExternalSecret template.
- **Chart signing** — `app/ocirepository.yaml` omits cosign `verify` pending
  confirmation of home-operations/konflate's chart signing identity; add it to
  match the `descheduler` pattern once known.

## Hardening follow-ups

- Swap the shared renovate App for a konflate-scoped one (least privilege —
  konflate now holds the same PEM renovate does, which carries write scope it
  never uses). This is the remaining over-grant; the credential's *lifecycle*
  is no longer a problem.
- Consider enabling `statusChecks` — **not** to duplicate CI, which already
  posts the diff as a sticky comment and gates merges on the required
  `flate successful` check, but because konflate surfaces blast-radius and
  danger-lint signals CI does not, and those currently live only in an
  SSO-gated UI someone has to remember to open. If added, leave it
  *non-required*: konflate is a single-instance in-cluster service, and a
  required check would couple merging to cluster health.
