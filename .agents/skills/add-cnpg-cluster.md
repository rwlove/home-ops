---
name: add-cnpg-cluster
description: Scaffold a new CNPG postgres cluster with Garage-backed barman backups
---

# Add CNPG Postgres Cluster

This skill scaffolds a new CloudNativePG cluster following the canonical
pattern in `kubernetes/apps/databases/cloudnative-pg/config/`. Every
cluster in the repo has the same five files plus a Flux Kustomization
appended to `cloudnative-pg/ks.yaml`. Backups go to Garage (S3)
through the barman-cloud plugin.

This is two halves. The DB half lives in `databases/`. The consuming
app — its own ExternalSecret pointing at `postgres-<app>-rw` — lives in
its own namespace under `kubernetes/apps/<ns>/<app>/app/`. This skill
covers both.

Canonical recent reference: `cloudnative-pg/config/cutvideo/`.

## Workflow

### Step 1: Collect details

Ask the user for:

1. **App name** — e.g. `cutvideo`, `videodupfinder`. Used as the
   directory name and as `<app>` in every reference below.
2. **App namespace** — where the consuming workload lives (e.g. `media`,
   `auth`). The DB always lives in `databases/`.
3. **Storage size** — default `5Gi` for config-shaped data. Bigger
   only if the user has a real estimate.
4. **Resource requests** — default `cpu: 100m`, `memory: 512Mi`. Bump
   for known-heavy workloads (Immich, Nextcloud).
5. **PG version** — default the most recent `ghcr.io/cloudnative-pg/postgresql`
   tag in use elsewhere in `config/` (currently `17.5`). Don't pick a
   newer tag without confirming the operator supports it.
6. **Cross-namespace TCP service?** — most apps don't need this; they
   talk to `postgres-<app>-rw.databases.svc.cluster.local`. Add a
   `service.yaml` only if the consumer is outside the cluster or
   needs a fixed LB IP. See `config/immich/service.yaml`.

### Step 2: Confirm prerequisites

Before generating files, verify:

- **1Password entry named `<app>`** with at minimum:
  - `GARAGE_AWS_ACCESS_KEY_ID`
  - `GARAGE_AWS_SECRET_ACCESS_KEY`
  - `POSTGRES_USER` (app DB user, used by the consumer)
  - `POSTGRES_PASS` (app DB password)
  - Plus any other env keys the consuming app needs (`*_DATABASE_URL`
    template variables).
- **Garage bucket** `postgres-<app>-backup` exists, with the access
  key from 1Password permitted to write to it. Garage doesn't
  auto-provision buckets — confirm this with the user. If not, they
  need to create it via the Garage CLI before applying.
- **`cloudnative-pg` 1Password entry** is already in place — every
  consumer ExternalSecret pulls `POSTGRES_SUPER_PASS` from it for the
  init flow.

If any of these are missing, stop and tell the user. Don't generate
files that will land in CrashLoopBackOff.

### Step 3: Generate the DB-side files

Create `kubernetes/apps/databases/cloudnative-pg/config/<app>/` with
five files. Substitute `<app>` everywhere it appears.

#### `cluster.yaml`

```yaml
---
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: postgres-<app>
spec:
  instances: 2

  imageName: ghcr.io/cloudnative-pg/postgresql:17.5

  primaryUpdateStrategy: unsupervised

  postgresql:
    parameters:
      pg_stat_statements.max: "10000"
      pg_stat_statements.track: all
      timezone: "America/New_York"

  storage:
    size: <SIZE>
    storageClass: ceph-block

  resources:
    requests:
      cpu: <CPU>
      memory: <MEM>

  enableSuperuserAccess: true
  superuserSecret:
    name: cloudnative-pg

  monitoring:
    enablePodMonitor: true

  plugins:
    - name: barman-cloud.cloudnative-pg.io
      isWALArchiver: true
      parameters: &params
        barmanObjectName: garage-<app>
        serverName: postgres-<app>-backup

  # bootstrap:
  #   recovery:
  #     source: source

  externalClusters:
    - name: source
      plugin:
        name: barman-cloud.cloudnative-pg.io
        parameters: *params
```

The commented `bootstrap.recovery` block is intentional — it stays as
a comment for normal new clusters and gets uncommented on disaster
recovery. Don't remove it.

#### `objectstore.yaml`

```yaml
---
apiVersion: barmancloud.cnpg.io/v1
kind: ObjectStore
metadata:
  name: garage-<app>
spec:
  configuration:
    data:
      compression: bzip2
    destinationPath: s3://postgres-<app>-backup/
    endpointURL: http://garage.storage.svc.cluster.local:3900
    s3Credentials:
      accessKeyId:
        name: <app>-secret
        key: AWS_ACCESS_KEY_ID
      secretAccessKey:
        name: <app>-secret
        key: AWS_SECRET_ACCESS_KEY
    wal:
      compression: bzip2
      maxParallel: 4
  retentionPolicy: 30d
```

#### `scheduledbackup.yaml`

```yaml
---
apiVersion: postgresql.cnpg.io/v1
kind: ScheduledBackup
metadata:
  name: postgres-<app>-backup
spec:
  schedule: "@weekly"
  immediate: true
  backupOwnerReference: self
  cluster:
    name: postgres-<app>
```

#### `externalsecret.yaml` (in `databases/`)

This is *the S3-creds secret only* — used by the ObjectStore. The
consumer app's DB credentials live in a different ExternalSecret in
the consuming app's namespace.

```yaml
---
# yaml-language-server: $schema=https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/external-secrets.io/externalsecret_v1.json
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: <app>
spec:
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword-connect
  target:
    name: <app>-secret
    template:
      data:
        # S3
        AWS_ACCESS_KEY_ID: "{{ .GARAGE_AWS_ACCESS_KEY_ID }}"
        AWS_SECRET_ACCESS_KEY: "{{ .GARAGE_AWS_SECRET_ACCESS_KEY }}"
  dataFrom:
    - extract:
        key: <app>
```

#### `kustomization.yaml`

```yaml
---
# yaml-language-server: $schema=https://json.schemastore.org/kustomization
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ./cluster.yaml
  - ./externalsecret.yaml
  - ./objectstore.yaml
  - ./scheduledbackup.yaml
```

If you added `service.yaml` for cross-namespace TCP, list it here too
in alphabetical order.

### Step 4: Wire into Flux

Append a new Kustomization block to
`kubernetes/apps/databases/cloudnative-pg/ks.yaml` (do not create a new
file). Use the same shape every other entry uses:

```yaml
---
# yaml-language-server: $schema=https://raw.githubusercontent.com/fluxcd-community/flux2-schemas/main/kustomization-kustomize-v1.json
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: <app>
  labels:
    substitution.flux.home.arpa/enabled: "true"
spec:
  targetNamespace: databases
  path: ./kubernetes/apps/databases/cloudnative-pg/config/<app>
  interval: 30m
  timeout: 5m
  prune: true
  wait: true
  sourceRef:
    kind: GitRepository
    name: home-ops-kubernetes
    namespace: flux-system
  dependsOn:
    - name: cloudnative-pg
      namespace: databases
    - name: garage
      namespace: storage
    - name: rook-ceph-cluster
      namespace: rook-ceph
  healthCheckExprs:
    - apiVersion: postgresql.cnpg.io/v1
      kind: Cluster
      current: status.phase in ['Cluster in healthy state']
```

Apps that recover from a backup or reference the barman-cloud plugin
directly also depend on `cloudnative-pg-plugin-barman-cloud` (see
`netbox`, `nextcloud`). Default new clusters don't need that.

### Step 5: Generate the consumer-side ExternalSecret

In the consuming app's directory (`kubernetes/apps/<ns>/<app>/app/`),
add an ExternalSecret that pulls *both* the `cloudnative-pg`
superuser entry and the per-app entry. The app needs a regular DB
user for connections AND the super pass for the cnpg-init flow that
sets up DB-level permissions.

```yaml
---
# yaml-language-server: $schema=https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/external-secrets.io/externalsecret_v1.json
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: <app>
spec:
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword-connect
  target:
    name: <app>-secret
    template:
      engineVersion: v2
      data:
        # App
        <APP>_DATABASE_URL: postgres://{{ .POSTGRES_USER }}:{{ .POSTGRES_PASS }}@postgres-<app>-rw.databases.svc.cluster.local:5432/<app>

        # Postgres Init
        INIT_POSTGRES_DBNAME: <app>
        INIT_POSTGRES_HOST: postgres-<app>-rw.databases.svc.cluster.local
        INIT_POSTGRES_USER: "{{ .POSTGRES_USER }}"
        INIT_POSTGRES_PASS: "{{ .POSTGRES_PASS }}"
        INIT_POSTGRES_SUPER_PASS: "{{ .POSTGRES_SUPER_PASS }}"
  dataFrom:
    - extract:
        key: cloudnative-pg
    - extract:
        key: <app>
```

The `INIT_POSTGRES_*` block is consumed by an `initContainers:` entry
in the consuming HelmRelease (typical pattern: a `mendhak/init-pg`-style
container that creates the DB and grants it to the app user). If the
app already exists and just needs DB env wiring, look at
`media/cutvideo/app/helmrelease.yaml` for the canonical init-container
shape.

### Step 6: Smoke test before declaring done

After Flux reconciles:

```sh
# Cluster phase + readiness
kubectl get cluster.postgresql.cnpg.io -n databases postgres-<app> \
  -o custom-columns=NAME:.metadata.name,INSTANCES:.spec.instances,READY:.status.readyInstances,STATUS:.status.phase

# Or use the helper
tools/check-postgres-dbs.sh

# Verify the WAL archiver is shipping to Garage
kubectl get backup -n databases -l cnpg.io/cluster=postgres-<app>

# Tail logs if anything is unhappy
kubectl logs -n databases -l cnpg.io/cluster=postgres-<app> -c postgres --tail=50
```

Expect to see `STATUS: Cluster in healthy state` and at least one
`Backup` resource in `completed` phase shortly after creation
(`scheduledbackup.spec.immediate: true` triggers one on apply).

If the cluster sits in `Setting up primary` for more than ~2 min,
check the ObjectStore status — bad Garage credentials are the most
common failure:

```sh
kubectl describe objectstore -n databases garage-<app>
```

### Step 7: Commit

Two commits, in this order, so Flux can reconcile cleanly:

1. `feat(databases): add postgres cluster for <app>` — the DB-side
   files plus the `ks.yaml` entry.
2. `feat(<ns>): wire <app> to its postgres cluster` — the consumer-side
   ExternalSecret and HelmRelease changes.

You can do them as one commit if both halves go in together, but the
two-commit form makes rollback finer-grained.

## Common variations

- **Larger workloads** (Immich, Nextcloud): bump `instances: 3` for
  HA, `storage.size`, and `resources.requests` substantially. See
  `config/immich/cluster.yaml`.
- **Cross-namespace LB exposure**: add `service.yaml` with the
  `lbipam.cilium.io/ips` annotation and a substitution variable for
  the IP. See `config/immich/service.yaml`. Also include `service.yaml`
  in the local `kustomization.yaml`.
- **Bootstrap from a barman backup** (cluster recovery): uncomment the
  `bootstrap.recovery` block in `cluster.yaml`. The `externalClusters`
  entry is already present and shares parameters with the WAL
  archiver via the `&params` anchor.

## What to skip

- Don't add a `helmrepository.yaml` or `helmrelease.yaml` — these
  clusters are CRDs from the operator, not Helm releases.
- Don't add the cluster files to a HelmRelease's `valuesFrom`. The
  consumer reads the secret directly.
- Don't write `namespace: databases` in any of the cluster-side
  manifests; the Flux Kustomization sets `targetNamespace`. (Per
  `flux.sorting.instructions.md`.)
