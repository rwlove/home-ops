# claude-runner — cron-driven Claude Code workflows (subscription)

A revival of the retired `automation/claude-runner`, re-homed to the `ai`
namespace and updated for **subscription auth** (never the API). It runs
headless `claude --print` workflows on a schedule so "check for events
occasionally and act" work doesn't depend on a laptop.

Design decisions live in the plan (`moonlit-brewing-dijkstra`). The load-bearing
ones:

- **Subscription only, never the API.** Auth is `CLAUDE_CODE_OAUTH_TOKEN` from
  `claude setup-token` (rides Max). No `ANTHROPIC_API_KEY` anywhere.
- **No MCP broker.** The Kuadrant broker has no enforceable per-tool authz and
  fronts mutating backends; its own note says do not point an unattended loop
  at it. This runner reads cluster state the enforceable way — **read-only
  Prometheus** (`cnp-allow.yaml`). Future API needs get scoped read-only RBAC,
  never the broker.
- **Two-tier routing.** Summarize / classify / triage / log-triage belong on
  **local models** (`sgpt`), per HOMELAB-SPEC Layer 6 — not here. This runner
  hosts only workflows that need Claude's reasoning + tool-use.

## Activation (shipped suspended — Gate 0)

The Flux `ks.yaml` ships `suspend: true`. Activate in order:

1. **Token.** On nomad: `claude setup-token` → copy the value →
   `op item edit "claude-runner" --vault Kubernetes claude_code_oauth_token=<token>`.
   Subscription token only — never an API key. (Pushover creds are pulled from
   the separate 1P `Pushover` item; no other fields needed on `claude-runner`.)
2. **Image.** Confirm `ghcr.io/rwlove/claude-runner` is published and current
   (plan D3: add `tmux`, bump `CLAUDE_CODE_VERSION`); set the tag in the app
   manifests. Ships suspended, so nothing pulls until step 3.
3. **Unsuspend the ks** (remove `spec.suspend`). Flux creates the
   `claude-runner-secret` ExternalSecret and runs the **auth-spike Job**.
   Confirm: `kubectl -n ai logs job/claude-runner-auth-spike` prints
   `SUBSCRIPTION-HEADLESS-OK`. **If it fails or demands an API key, stop** —
   the subscription-headless assumption is void; re-decide per the plan.
4. **Unsuspend the CronJob** (remove `spec.suspend` from
   `cronjob-flux-longhorn-drift-digest.yaml`). Test once with
   `kubectl -n ai create job --from=cronjob/claude-runner-flux-longhorn-drift-digest drift-test`.

## Workflows

| Workflow | Schedule (UTC) | Tier | What |
|---|---|---|---|
| `flux-longhorn-drift-digest` | `0 13 * * 1` (Mon 09:00 EDT) | Claude | Read Flux/Longhorn health from Prometheus, reason about real drift + a fix, send ONE Pushover digest (private — reuses the alertmanager Pushover app). Silent when clean. |
| `adoption-scout` | `0 14 * * 3` (Wed 10:00 EDT) | Claude | Shallow-clone the repo, review recently-bumped charts/images for adoptable new features (verified against the DEPLOYED version), and re-check `# workaround:` annotations + `workaround`-labeled issues for retirement. Posts ONE **public GitHub issue** (create-or-comment, label `adoption-scout`), naming only public-tier apps. Discovery-only — never opens PRs. Silent when clean. Kill if useful-output rate <30% after 2 weeks. |

Summarize/triage-style checks (Renovate-PR summaries, log skims, doc-drift)
do **not** go here — they are the local-model (`sgpt`) tier per HOMELAB-SPEC
Layer 6.

### `adoption-scout` — extra one-time setup

Beyond Gate 0 (token + image + unsuspend), the scout needs:

1. **GitHub write token.** A fine-grained PAT scoped to **Issues: Read+Write on
   `rwlove/home-ops` only** (nothing else), stored on the 1P `claude-runner`
   item: `op item edit "claude-runner" --vault Kubernetes github_issue_token=<pat>`.
   The runner shell (not Claude) uses it to post the digest.
2. **Label.** Create the `adoption-scout` GitHub label once — GitHub rejects
   issue creation with a non-existent label:
   `gh label create adoption-scout -R rwlove/home-ops -c FBCA04 -d "weekly adoption/workaround digest"`.
3. **Image deps.** The runner image must ship `git` (for the shallow clone) and
   `jq` (to build the issue JSON). Verify in the test run below; if `jq` is
   missing, add it in the `containers` repo and bump the tag.
4. **Test suspended → unsuspend.**
   `kubectl -n ai create job --from=cronjob/claude-runner-adoption-scout adoption-scout-test`,
   confirm it clones, produces a two-section digest naming **no** restricted-tier
   apps, and opens exactly one labeled issue (a second run comments, not
   duplicates), then remove `spec.suspend`.

## Interactive shell (survives laptop sleep)

`deployment-shell.yaml` runs a persistent `claude-runner-shell` Deployment that
holds a detached `tmux` session on the subscription token. Use it to kick off a
long interactive task and walk away — the session keeps running in-cluster when
your laptop sleeps.

```sh
kubectl -n ai exec -it deploy/claude-runner-shell -- tmux attach -t main
# then inside: claude
# detach: Ctrl-b d   (session + any running task persist; reattach anytime)
```

HOME (`/home/node`) is a `ceph-block` PVC (`claude-shell-data`), so Claude
transcripts + scratch clones survive pod restarts. Needs the tmux-bearing image
(`ghcr.io/rwlove/claude-runner:v2.1.240+`, built with `tmux` in the `containers`
repo). Egress is the same CNP as the cron workflows (read-only Prometheus +
world:443); attach is via `kubectl exec`, no route.

## Vault bridge — the Obsidian vault as files in the pod (Phase 3)

`deployment-shell.yaml` runs a **`vault-bridge` sidecar** next to the `shell`
container so the in-cluster Claude sees the same `~/vaults/claude` files as the
laptop. It mounts into the shell at **`/home/node/vaults/claude`**.

**How it works.** The laptop's Obsidian syncs the vault into CouchDB
(`obsidian.${SECRET_DOMAIN}`) via the Self-hosted LiveSync plugin. The sidecar
runs the plugin author's **official** headless CLI — `ghcr.io/vrtmrz/livesync-cli`,
same repo and core as the plugin — in its `daemon` mode: an initial mirror scan
then continuous bidirectional replication between CouchDB and the local
filesystem (CouchDB → files via the `_changes` feed; files → CouchDB via a file
watcher). It talks to CouchDB over the **in-cluster** Service
`obsidian-couchdb.collab.svc.cluster.local:5984` (not the public route).

**Topology: sidecar, not standalone.** The bridge and the shell share one RWO
`ceph-block` PVC (`claude-vault-data`) inside a single pod — `/db` (the CLI's
PouchDB database) and `/vault` (the materialised `.md` files) are subPaths of
it. A standalone bridge Deployment would need the vault PVC to be RWX, and this
cluster's only RWX pattern is direct-NFS (not ceph/Longhorn), so the sidecar is
the deliberate choice. The vault is regenerable from CouchDB, so `ceph-block`
(rule 2) is correct; CouchDB stays the source of truth.

**⚠ CouchDB is canonical; the pod is a SECONDARY writer.** LiveSync is
eventually-consistent. If pod-Claude and the laptop edit the *same note* at the
same time, LiveSync records **conflict revisions** rather than losing data —
resolve them from Obsidian on the laptop (the conflict-resolution UI). Treat the
in-cluster copy as a working mirror, not the primary.

### Verify (comes up on merge)

No extra activation step: the db name (`claude`) and `encrypt: false` are inline
in `externalsecret-vault-bridge.yaml` — the `claude` vault is **not** E2E-encrypted
(verified against the live db, plaintext docs) — and the CouchDB creds
(`couchdb_username`/`couchdb_password`) are already in the 1P `obsidian` item. On
merge the shell pod rolls once (Recreate) and the `vault-bridge` sidecar connects
and materialises the vault. Confirm:

```sh
kubectl -n ai logs deploy/claude-runner-shell -c vault-bridge
kubectl -n ai exec deploy/claude-runner-shell -c shell -- ls /home/node/vaults/claude
```

> **Merge disruption:** the shell Deployment is `strategy: Recreate`, so merging
> this Phase-3 change replaces the pod once — the running tmux session is lost
> (start it fresh after the roll). Land it in a routine window.

### Network

- `cnp-allow.yaml` (ai ns) adds an egress hole to `obsidian-couchdb:5984` in
  `collab`. Both containers share the pod's Cilium identity
  (`app.kubernetes.io/name: claude-runner`), so the hole covers the sidecar.
- `obsidian-couchdb/app/cnp-allow.yaml` (collab ns) adds the matching ingress
  rule from `ai`/`claude-runner`. Both namespaces are default-deny; DNS is
  already granted by the baseline component.

## Routing, recipe, quota & kill criteria

**When** to push work into this runner (vs. `local-cron` / interactive / not-automated),
the step-by-step **recipe** for adding a Claude-tier workflow, **quota** governance, and
**kill criteria** all live in the routing policy — the single source of truth:

**[`.agents/instructions/claude-runner-routing.md`](../../../../.agents/instructions/claude-runner-routing.md)**

> That policy is a **provisional trial** (review ~2026-09-21) and documents the one-PR
> revert if the approach isn't earning its keep.
