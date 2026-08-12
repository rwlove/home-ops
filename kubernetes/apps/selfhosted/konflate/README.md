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

konflate authenticates to GitHub with a **short-lived App installation token**,
minted by a `GithubAccessToken` generator from the same GitHub App
(`2931213`) renovate-operator uses — see `app/externalsecret.yaml`. No standing
PAT. The App's PEM comes from the 1Password `Github` item
(`renovate_app_private_key`), replicated into this namespace as the
`github-app-pem` secret. konflate stays read-only (`prComments`/`statusChecks`
off), so the App's write scope is unused.

**The token expires after 1 hour, and the pod must be rolled to pick up a new
one.** The chart wires `secret.existingSecret` in through `envFrom`, and a
pod's environment variables are fixed at start — so konflate cannot re-read a
rotated Secret in place. `deploymentAnnotations` therefore carries
`reloader.stakater.com/auto: "true"`, and Reloader (kube-system) rolls the
Deployment each time `konflate-secret` changes. Restart cadence equals the
ExternalSecret's 30m `refreshInterval`.

Without that annotation konflate authenticates for exactly one hour after
each start and then 401s indefinitely, which is how it sat broken for 3.7
days from 2026-08-08: probes are process-level and kept passing, the pod
never restarted, and ESO reported `Ready=True` because minting the token
had in fact succeeded. `app/prometheusrule.yaml` alerts on the two counters
that did show it (`konflate_forge_list_errors_total`,
`konflate_diff_jobs_total{result="error"}`).

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

- Swap the shared renovate App token for a dedicated read-only PAT or a
  konflate-scoped GitHub App (least privilege; the current token can write
  though konflate never does).
- Ask upstream to support App auth on the **read** path. konflate already
  mints and refreshes installation tokens internally when given
  `config.appClientId` + `secret.appPrivateKey`, but the chart documents that
  pair as the *write* identity — a read-only instance like this one can't use
  it, which is why the read token has to arrive through a Secret and be
  reloaded. Native read-path App auth would remove the expiry problem, and
  with it the 30m restart cadence, entirely.
