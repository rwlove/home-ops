# health — personal health dashboard

A single-page, Authelia-gated dashboard (`health.thesteamedcrab.com`) that charts
personal health markers over time. An unprivileged nginx serves the static page
and reverse-proxies two **read-only** PostgREST sidecars on localhost, so the
page's `fetch()` calls stay same-origin behind the gate.

## Layout

| Path | Purpose |
| ---- | ------- |
| `app/resources/index.html` | The dashboard — generic uPlot charts, fetched at runtime |
| `app/resources/default.conf` | nginx: static serve + `/api/mk/` and `/api/pump/` proxies |
| `app/resources/db-setup.sql` | Source-of-record for the read-only DB views + `health_ro` grants |
| `app/helmrelease.yaml` | app-template HelmRelease (nginx + two PostgREST sidecars) |

## Views (read-only, `health_ro` role)

- `api.v_bp` — blood pressure over time (from Medikeep)
- `api.v_dose` — medication dose timeline (from Medikeep)
- `api.v_weight` — body weight (PUMP-fed)

## Charts

The page is **generic and runtime-fetched** — series labels, medication names, and
every value come from the DB at request time. Cards: body-weight vs medication
dose; a blood-pressure trend (readings with 7- and 30-day rolling averages, plus
shaded ACC/AHA systolic category bands and diastolic guide lines); and a
weight↔blood-pressure correlation scatter.

**Data classification (Restricted):** no reading, dose, medication name, or other
personal value is ever committed to this public repo. `index.html` carries only
layout and *public* clinical thresholds (ACC/AHA BP categories); all personal data
is fetched live behind Authelia. Do not hardcode any value here.

## Ownership

Owned by **Crusher** (the medical-records steward). Crusher curates the dashboard
and runs the **weekly trend review** (`local-cron-crusher-review`, kagent tier):
a scheduled read-only pull of the three views, a trend/correlation interpretation
by the in-cluster Crusher agent (local model), delivered to a private sink. The
app's *deployment* (HelmRelease, CNPG wiring, Flux) is home-ops/platform; Crusher
owns the dashboard content and the review, not the manifests' plumbing.
