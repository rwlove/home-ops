---
name: provision-cnpg-garage-backup
description: Provision the Garage bucket + S3 key + 1Password creds for a CNPG cluster's Barman backups — the out-of-git half of the cnpg-app-database pattern, done agent-driven (storage-operator) instead of by hand. Use whenever a new CNPG database is added (invoked from add-cnpg-cluster Step 2) or to backfill backups for any cluster whose ObjectStore/ExternalSecret is stuck for lack of creds.
---

# Provision CNPG Garage Backup

The `components/cnpg-app-database` Component renders an `ObjectStore`
(`garage-<app>`), a `ScheduledBackup`, and an `ExternalSecret` from `${APP}`.
Three things must exist **outside git** before the Flux Kustomization for a new
cluster will go Ready — the Component does **not** create them:

1. A **Garage bucket** `postgres-<app>-backup`.
2. A **Garage S3 key** granted read+write on that bucket.
3. A **1Password item** named `<app>` (Kubernetes vault) carrying
   `GARAGE_AWS_ACCESS_KEY_ID` + `GARAGE_AWS_SECRET_ACCESS_KEY`.

The ExternalSecret does `dataFrom.extract.key: <app>` and templates those two
fields into `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`. **If either GARAGE field
is missing the template fails entirely** (`map has no entry for key …`), the
whole `<app>-secret` is never written, and both the `databases/<app>` and the
consuming app's Kustomizations hang (the latter `dependsOn` the former). So this
is not "backups pending" — a missing cred is a hard deploy block.

This skill provisions all three, verifies the sync, and seeds a first base backup
so there's a recovery point immediately (not after the next `@weekly` run).

Historically these three were a **manual prerequisite** (see `add-cnpg-cluster`
Step 2 and the `garage-cnpg-provisioning-playbook` memory). This skill makes the
storage-operator do them. Run it as part of every new CNPG cluster.

## Prerequisite: a write-capable 1Password identity (one-time)

Both agent-usable 1Password identities are **read-only** and cannot be changed
from CLI/app:

- the `op` service account (`OP_SERVICE_ACCOUNT_TOKEN`) → `op item create/edit`
  returns `(101) You do not have permission`;
- the Connect token (`onepassword-connect`, `external-secrets` ns) → the REST API
  returns `403 … does not have permission to perform create`.

Editing an **existing** service account's grant is **web-console only**
(`op service-account` has only `create`/`ratelimit`). So agent-driven writes need
a dedicated write SA whose token the read-only SA can fetch and use inline.

**One-time setup (Rob, from a human `op` session):**

```bash
# human session — the SA token must be out of the way, then sign in as you:
unset OP_SERVICE_ACCOUNT_TOKEN
op signin                                    # uses the 1Password desktop-app integration
op whoami                                    # must show your USER, not SERVICE_ACCOUNT

# mint a write-capable SA scoped to the vault that holds backup creds:
op service-account create cnpg-backup-writer \
  --vault "Kubernetes:read_items,write_items" --raw          # prints the token ONCE

# save the token so the read-only SA (and this skill) can fetch it:
op item create --category "API Credential" --title cnpg-backup-writer \
  --vault Kubernetes "token[password]=<paste-the-token>"
```

> **Blast radius (surface this to Rob):** a `Kubernetes:write_items` token readable
> by any agent means any agent can write/overwrite *any* secret in that vault. The
> least-privilege alternative is a dedicated `CNPG-Backups` vault
> (`--vault "CNPG-Backups:read_items,write_items"`) holding only garage creds,
> with the Component's ExternalSecret reading garage creds from there (needs a
> second ClusterSecretStore scoped to that vault). Prefer that if hardening
> matters; the Kubernetes-vault form above is the quick path.

The skill below reads this token and uses it **only for the write**, never
exporting it into the environment:

```bash
op_write() {  # usage: op_write <op item args...>
  local T; T="$(op read 'op://Kubernetes/cnpg-backup-writer/token' 2>/dev/null)" \
    || { echo "no write token — run the one-time setup in this skill"; return 1; }
  OP_SERVICE_ACCOUNT_TOKEN="$T" op "$@"
}
```

If the write token isn't present, **stop and tell Rob to run the one-time setup**
(or hand him the `env -u OP_SERVICE_ACCOUNT_TOKEN op item edit …` one-liner to do
the single write from his own login). Don't fabricate creds or leave the cluster
half-provisioned silently.

## Convention (current — 2026-10)

Recent apps (lubelogger, ghostfolio, monica, wardrowbe) use:

- **key name = bare `<app>`** (not `<app>-key`)
- grant = **`--read --write`** scoped to `postgres-<app>-backup` only — **no
  `--owner`**, no bucket-create capability.

This **supersedes** the older `<app>-key` + `--owner` form in the
`garage-cnpg-provisioning-playbook` memory. Match a recently-provisioned app if in
doubt: `garage key info <recent-app>`.

## Steps

```bash
APP=<app>
BUCKET="postgres-${APP}-backup"
GARAGE_POD=$(kubectl get pod -n storage -l app=garage -o name | head -1 | cut -d/ -f2)
g() { kubectl exec -n storage "$GARAGE_POD" -- /garage "$@"; }
```

### 1. Bucket (idempotent)

```bash
g bucket list | grep -qw "$BUCKET" || g bucket create "$BUCKET"
```

### 2. Key (idempotent) — capture creds

```bash
if g key list | grep -qw "$APP"; then
  # Key exists but the SECRET is not re-displayable from Garage. If this is a
  # re-run and the 1P item already has the GARAGE fields, reuse them (skip to
  # step 4). If the secret is truly lost, rotate: `g key delete <id>` then
  # recreate — propose this to Rob first (it invalidates the old creds).
  echo "key $APP already exists — reuse 1P creds or rotate (propose first)"
else
  g key create "$APP"        # prints Key ID + Secret — the secret shows ONCE
fi
# capture KEY_ID and SECRET from the create output
```

### 3. Grant RW on the bucket (idempotent)

```bash
g bucket allow --read --write "$BUCKET" --key "$APP"
```

### 4. Write the 1Password item (uses the write token)

The item is **PASSWORD category**; a Password item will not save with an empty
`password` field (`Password item requires ps value`), so always include a
throwaway `password` when creating.

```bash
# create if missing, else add/update the two fields in place (preserving others):
if op item get "$APP" --vault Kubernetes >/dev/null 2>&1; then
  op_write item edit "$APP" --vault Kubernetes \
    "GARAGE_AWS_ACCESS_KEY_ID[text]=$KEY_ID" \
    "GARAGE_AWS_SECRET_ACCESS_KEY[text]=$SECRET"
else
  op_write item create --category password --vault Kubernetes --title "$APP" \
    "password=$(openssl rand -base64 32)" \
    "GARAGE_AWS_ACCESS_KEY_ID[text]=$KEY_ID" \
    "GARAGE_AWS_SECRET_ACCESS_KEY[text]=$SECRET"
  # a brand-new app also needs POSTGRES_USER/POSTGRES_PASS + any app keys —
  # see add-cnpg-cluster Step 2. Add them in the same create when known.
fi
```

> Never print `$SECRET` to a public artifact. The 1P item, memory, and this
> session are internal; a PR/commit/log is not.

### 5. Verify the ExternalSecret syncs

```bash
kubectl -n databases annotate externalsecret "$APP" force-sync="$(date +%s)" --overwrite
kubectl -n databases get externalsecret "$APP" \
  -o jsonpath='{.status.conditions[*].reason}{"\n"}'   # want: SecretSynced
kubectl -n databases get secret "${APP}-secret" \
  -o jsonpath='{.data}' | jq -r 'keys|join(", ")'      # want: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, …
```

If it still shows `SecretSyncedError: map has no entry for key …`, a GARAGE field
is missing/misnamed in the 1P item — fix the field name, re-sync.

### 6. Seed a base backup (so a recovery point exists now)

`ScheduledBackup` is `@weekly`; until it runs there is **no** recovery point even
with WAL archiving on. Seed one on demand (additive — new CR, no deletes):

```bash
kubectl apply -f - <<EOF
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata:
  name: postgres-${APP}-backup-seed-$(date +%Y%m%d)
  namespace: databases
spec:
  cluster: {name: postgres-${APP}}
  method: plugin
  pluginConfiguration: {name: barman-cloud.cloudnative-pg.io}
EOF

# confirm (note: `backup` is ambiguous with longhorn.io — use the full kind):
kubectl -n databases get backup.postgresql.cnpg.io \
  "postgres-${APP}-backup-seed-$(date +%Y%m%d)" \
  -o jsonpath='{.status.phase}{"\n"}'   # want: completed
```

## Done when

- `garage bucket list` shows `postgres-<app>-backup`; `garage bucket info` shows
  the `<app>` key with RW.
- The `<app>` 1P item has both `GARAGE_AWS_*` fields.
- `ExternalSecret/databases/<app>` is `SecretSynced` and `<app>-secret` carries
  the AWS keys.
- A `Backup.postgresql.cnpg.io` for the cluster is `completed`
  (`ObjectStore.status.serverRecoveryWindow` is no longer empty).

## Re-run safety

Every step is idempotent (bucket/key/grant check-before-create; 1P edit-in-place;
`force-sync` is harmless; the seed Backup name is date-stamped). The one
non-idempotent hazard is **key recreation** — a Garage key's secret can't be
re-read, so never blindly `key create` over an existing app key; reuse the 1P
creds or rotate deliberately (propose first).

## Audit coverage

To confirm *all* CNPG clusters have working backups (not just newly-provisioned
ones), the storage-operator's audit checks each cluster's
`ObjectStore.status.serverRecoveryWindow.lastSuccessfulBackupTime` +
`Cluster.status` LastBackupSucceeded/ContinuousArchiving — **not** the
`cnpg_collector_last_*_backup_timestamp` Prometheus metrics, which are frozen for
plugin-method clusters (see `reference_cnpg_plugin_backup_metric_blind_spot`).
