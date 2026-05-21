# Apiserver Audit Logging — Phase 5.P operator runbook

**Operator-side.** Per Inv 9, control-plane static-pod manifests are
not modified by agents. Agents prep the policy file + Vector pipeline;
the kubeadm patch + apiserver restart per node is yours.

## What this gives us

Per PSA labels shipped in PR #11615 plus the analysis in
`docs/src/pod_security_audit.md`, the cluster currently has
Pod-Security-Admission targeting per-namespace BUT no apiserver
`--audit-log-path` configured. Violations are computed and discarded;
the labels are inert.

After this runbook:

- PSA `audit-violations` and `enforce-policy` annotations appear in
  the audit log on every pod admission decision
- RBAC changes are recorded at RequestResponse level
- Namespace lifecycle events pair with the Kyverno 5.O3 enforce policy
  for blast-radius traceability
- Vector ships `/var/log/kubernetes/audit.log` to Loki for query

## Prerequisites

1. Audit policy committed at `kubernetes/control-plane-config/audit-policy.yaml`
   (already in the repo as of Phase 5.P prep PR)
2. Vector pipeline already shipping `/var/log/kubernetes/audit.log` to
   Loki (also in the Phase 5.P prep PR — gets it on each node via the
   agent DaemonSet that already mounts `/var/log` host path)

## Per-node procedure

**Run on ONE control-plane node at a time. Never two on the same day.**

### Step 1 — Copy the audit policy to the node

```sh
scp kubernetes/control-plane-config/audit-policy.yaml \
    root@masterN.${SECRET_DOMAIN}:/etc/kubernetes/audit-policy.yaml
```

Verify:

```sh
ssh root@masterN.${SECRET_DOMAIN} 'ls -la /etc/kubernetes/audit-policy.yaml'
```

### Step 2 — Create the audit log directory

```sh
ssh root@masterN.${SECRET_DOMAIN} 'mkdir -p /var/log/kubernetes && touch /var/log/kubernetes/audit.log'
```

### Step 3 — Patch the kube-apiserver static manifest

Open an SSH session, leave it open the whole time. If apiserver fails
to restart cleanly, this session is your rollback path.

```sh
ssh root@masterN.${SECRET_DOMAIN}
```

Then on the node:

```sh
cp /etc/kubernetes/manifests/kube-apiserver.yaml \
   /root/kube-apiserver.yaml.pre-audit
```

Edit `/etc/kubernetes/manifests/kube-apiserver.yaml` and add to the
`spec.containers[0].command` array (insert anywhere after `kube-apiserver`):

```yaml
- --audit-policy-file=/etc/kubernetes/audit-policy.yaml
- --audit-log-path=/var/log/kubernetes/audit.log
- --audit-log-maxage=14
- --audit-log-maxbackup=3
- --audit-log-maxsize=200
```

Add these volume + mount entries to the same Pod spec:

```yaml
# Under spec.volumes:
- name: audit-policy
  hostPath:
    path: /etc/kubernetes/audit-policy.yaml
    type: File
- name: audit-log
  hostPath:
    path: /var/log/kubernetes
    type: DirectoryOrCreate

# Under spec.containers[0].volumeMounts:
- name: audit-policy
  mountPath: /etc/kubernetes/audit-policy.yaml
  readOnly: true
- name: audit-log
  mountPath: /var/log/kubernetes
```

### Step 4 — Wait for kubelet to restart the apiserver

Kubelet watches the manifest file and restarts the apiserver pod
automatically when the file changes (mtime-based).

```sh
# Watch the apiserver pod restart
crictl ps --name kube-apiserver --all
# OR via journalctl
journalctl -u kubelet -f
```

Expected: `kube-apiserver` pod enters `Stopped` → new pod
`Running`. Total downtime ~30-60s on this one apiserver. The other 2
control-plane apiservers keep serving during this window.

### Step 5 — Verify the audit log is being written

```sh
ssh root@masterN.${SECRET_DOMAIN} 'tail -n 5 /var/log/kubernetes/audit.log'
```

You should see JSON audit events. If empty after 30s, something is
wrong — see Recovery below.

### Step 6 — Confirm Loki is receiving

From any host with kubectl access:

```sh
# Vector should be shipping; logs land with label app=kube-apiserver-audit
# or similar. Query Loki:
logcli query '{filename="/var/log/kubernetes/audit.log"}' --limit 5 --tail
```

### Step 7 — Wait at least 24 hours before doing the next node

This gives the audit log time to accumulate; if anything's off,
you'll see it before instrumenting the next master.

## Recovery — apiserver won't restart

If kubelet logs show the new apiserver crashlooping:

```sh
# Restore the pre-audit manifest:
cp /root/kube-apiserver.yaml.pre-audit /etc/kubernetes/manifests/kube-apiserver.yaml

# Kubelet picks up the change within ~10s. Apiserver returns to its
# pre-audit config.
```

Then debug from the logs. Common gotchas:

- **Audit policy YAML invalid** — apiserver rejects the policy file at
  startup; kubelet shows the apiserver pod stuck in CrashLoopBackOff
  with an error message about audit policy parse failure.
- **Audit log directory not writable** — apiserver fails to open the
  log file. Check ownership on `/var/log/kubernetes/`.
- **Volume mount missing** — apiserver can't read the policy file.
  Check the `volumes` + `volumeMounts` entries in the manifest.

## Schedule

| Step | Node | Earliest |
|---|---|---|
| 5.P1 | master1 | Any low-traffic evening |
| 5.P2 | master2 | ≥1 day after 5.P1 lands cleanly |
| 5.P3 | master3 | ≥1 day after 5.P2 lands cleanly |

Skip the next step if any prior step was anything other than
uneventful.

## Disk pressure

Audit log defaults: 200MB max per file, 3 backups, 14d retention.
That's up to 800MB per master = ~2.4GB across 3 masters. Tune via
the `maxage`/`maxbackup`/`maxsize` flags if disk pressure shows up.

## See also

- `kubernetes/control-plane-config/audit-policy.yaml` — the policy
- `docs/src/pod_security_audit.md` — what PSA already labels
- `~/vaults/claude/runbooks/home-ops/cluster_rebuild.md` — adjacent
  control-plane procedure
- `~/vaults/claude/runbooks/home-ops/master1_etcd_disk_swap.md` —
  prior precedent for master1-first instrumentation
- Memory: `project_psa_audit_privileged_is_noop` — the gap this closes
