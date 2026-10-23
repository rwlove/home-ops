# reactive-resume

Self-hosted resume builder ([Reactive Resume](https://rxresu.me), v6) in the
`selfhosted` namespace. Internal-only (`internal` Gateway); RR's own
email/password login is the gate. Resumes and accounts live in the CNPG cluster
`postgres-reactive-resume` (Barman→Garage DR); the `/app/data` PVC
(`ceph-block`) holds uploads only.

## Components

| Piece | Where |
| --- | --- |
| App (Deployment, `app-template`) | `app/helmrelease.yaml` |
| HTTPRoute (internal) | inline `route:` in the HelmRelease |
| Postgres (CNPG, 3 instances) | `../../databases/cloudnative-pg/config/reactive-resume/` |
| DB backup (Barman→Garage) | rendered by the `cnpg-app-database` component |

## Out-of-git provisioning (one-time, before first healthy reconcile)

The ExternalSecrets resolve against a 1Password **`reactive-resume`** item
(Kubernetes vault) and the shared **`cloudnative-pg`** item. Create the
`reactive-resume` item with these fields:

| Field | Value |
| --- | --- |
| `RR_DB_USER` | `reactive-resume` |
| `RR_DB_PASS` | generated (e.g. `openssl rand -hex 24`) |
| `AUTH_SECRET` | `openssl rand -hex 32` |
| `GARAGE_AWS_ACCESS_KEY_ID` | from the Garage key below |
| `GARAGE_AWS_SECRET_ACCESS_KEY` | from the Garage key below |

Garage backup bucket + key (per `.agents/skills/provision-cnpg-garage-backup.md`):
bucket `postgres-reactive-resume-backup`, a key with `--read --write` on it,
secret written into the two `GARAGE_*` fields above. `POSTGRES_SUPER_PASS` is
already present in the `cloudnative-pg` item (shared superuser).

## Account bootstrap + lockdown (after first healthy reconcile)

1. Browse to `https://reactive-resume.${SECRET_DOMAIN}` and register Robert's
   account, then a dedicated **`herman`** account (career-coach agent).
2. Capture Herman's credentials into 1Password (`reactive-resume` item,
   `HERMAN_EMAIL` / `HERMAN_PASSWORD`) — Herman logs in via the REST API for a
   JWT (see the herman persona access recipe).
3. Seal the instance: uncomment `FLAG_DISABLE_SIGNUPS: "true"` in
   `app/helmrelease.yaml` and merge. No new accounts after that.

## Notes

- **v6 needs no Chrome/Browserless** — PDF export is client-side since v5.1.
- `readOnlyRootFilesystem` is OFF for bring-up (Node/Nest app); tighten with
  tmpfs mounts after soak.
- Herman reaches the API over the LAN (`reactive-resume.${SECRET_DOMAIN}`) or a
  `kubectl port-forward`; no in-cluster Herman tier exists yet, so the CNP has
  no dedicated agent ingress rule.
