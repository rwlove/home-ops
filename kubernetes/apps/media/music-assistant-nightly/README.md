# music-assistant-nightly (THROWAWAY)

Isolated test instance for
[music-assistant/support#6023](https://github.com/music-assistant/support/issues/6023):
`config_set_player_value` coerced a stringified boolean
(`"false"`/`"0"`/`"off"`) with `bool(value)`, so any MCP-gateway write of a
`false`-valued on/off player setting was silently discarded as `True`. Fixed
upstream in `music-assistant/models#442`, shipping in nightly
`2.11.0.dev2026102203`. This app exists only to confirm the fix through the
MCP gateway path, then gets deleted. **Not Renee-facing. Not merged live
without Rob's sign-off.**

Ships with `spec.suspend: true` on every Flux Kustomization involved — Flux
will not create a single resource until that's flipped to `false` in a
follow-up commit. Do not unsuspend without Rob's explicit go-ahead.

## Design notes

- **Own scratch volume.** `ceph-block` PVC (`music-assistant-nightly-data-pvc`,
  2Gi), `reclaimPolicy: Delete` — confirmed against
  `kubernetes/apps/rook-ceph/rook-ceph/cluster/helmrelease.yaml`. Deleting the
  PVC at teardown deletes the backing RBD image; no manual cleanup, no
  orphaned volume. Never touches prod's `music-assistant-data-pvc`
  (Longhorn) or `music-assistant-music-pvc` (NFS music library) — this
  instance mounts neither.
- **No IoT-VLAN macvlan.** Prod's `music-assistant-iot-static` net-attach
  exists so MA gets an L2 presence on VLAN 20 for AirPlay/zeroconf discovery
  of the real multi-protocol LAN player used in Rob's original repro. Giving
  the nightly the same macvlan would put **two MA instances doing
  zeroconf/AirPlay discovery on the same IoT VLAN simultaneously** — a real
  risk to the live MA/HA audio path (the operator prime directive this repo
  runs under) for no good reason, since the upstream bug is about the
  generic on/off `PlayerConfig` write path, not about any specific provider.
  **Recommendation: use MA's built-in browser/web player instead** — opening
  the nightly's web UI (via `kubectl port-forward`, see the runbook) and
  clicking "Play on this device" registers a software-only player with zero
  hardware and zero network presence beyond the ClusterIP Service already
  in this HelmRelease. This is the test runbook's path. Confidence here is
  ~75%: I have not independently re-verified the browser/web player exposes
  a boolean on/off `PlayerConfig` entry on this exact nightly build — confirm
  it in the UI (step in the runbook) before scripting the MCP calls, and if
  it turns out the browser player doesn't carry a settable on/off field,
  come back to this README and reopen the VLAN-route discussion with Rob
  rather than silently wiring up the macvlan.
- **Security context.** `readOnlyRootFilesystem: true` + the rest of
  `.agents/instructions/helmrelease.security.md`'s baseline is applied, but
  **unverified** against this image/tag — prod's HelmRelease runs a writable
  rootfs with no documented reason, so there's no prior evidence MA
  tolerates a read-only one. Tmpfs mounts cover `/tmp`, `/.audio` (prod
  writes there), `/.cache`, `/.config` (HOME=`/`, non-root uid 1113). If the
  pod CrashLoops with an EROFS/"Read-only file system" error in its logs,
  flip `readOnlyRootFilesystem: false` for this one container as the fast
  unblock — acceptable for a throwaway instance, but say so in the PR/commit
  so it doesn't quietly become the template for the next one.
- **Egress closed.** No `world` egress rule at all — unlike prod (which
  needs Spotify/Tidal/Sonos/mDNS egress), this instance has no provider that
  should ever reach a real device or external API during the test. Only the
  namespace-wide baseline (DNS, apiserver, host probes, intra-namespace) and
  the two mcp-gateway ingress rules apply.
- **Digest-pinned, not `:nightly`.** `2.11.0.dev2026102203@sha256:8af16c0713bbcb2dc1d2819faf40c51fa3bf8f4710b760f79b2d7bb2b8a629bc`
  (resolved 2026-10-22 via `skopeo inspect docker://ghcr.io/music-assistant/server:2.11.0.dev2026102203`).
  A floating `:nightly` tag would drift the moment upstream publishes again.

## Test runbook

### 0. Pre-flight (Rob)

1. Review the PR diff on `claude/ma-nightly-6023-test`.
2. Confirm `kubectl kustomize kubernetes/apps/media/music-assistant-nightly/app`
   and `kubectl kustomize kubernetes/apps/mcp-system/music-assistant-nightly-mcp/{app,mcp}`
   still build clean, and that `git diff origin/main -- kubernetes/apps/media/music-assistant kubernetes/apps/mcp-system/music-assistant-mcp`
   is empty (prod untouched).
3. Merge. With both `suspend: true`, this is a no-op on the live cluster —
   Flux creates nothing.

### 1. Go live for the test window

1. Flip `suspend: true` → `suspend: false` in both:
   - `kubernetes/apps/media/music-assistant-nightly/ks.yaml`
   - `kubernetes/apps/mcp-system/music-assistant-nightly-mcp/ks.yaml`

   Commit + push directly to `main` (small, reviewable, no `kubectl apply`
   bypass of Flux — keeps the "cannot break GitOps" prime directive intact).
2. `flux reconcile kustomization music-assistant-nightly -n flux-system`
   (and the two mcp-system Kustomizations) to force the sync instead of
   waiting out the interval.
3. Confirm the pod is Ready and not crash-looping:
   `kubectl -n media get pods -l app.kubernetes.io/name=music-assistant-nightly`
   `kubectl -n media logs -l app.kubernetes.io/name=music-assistant-nightly --tail=50`
4. Confirm the registration landed:
   `kubectl -n mcp-system get mcpserverregistration music-assistant-nightly-tools`
   and that the gateway now lists `manightly_*` tools (check via whatever
   discovery surface you normally use against `lovenet-gateway` —
   `discover_tools` / the gateway's own tool-list endpoint).

### 2. Register a test player — no hardware, no VLAN

1. `kubectl -n media port-forward svc/music-assistant-nightly 8095:8095`
2. Open `http://localhost:8095` in a browser.
3. In the MA web UI, use the built-in browser/web player ("Play on this
   device" or equivalent) once. This registers a software player with no
   external device and no VLAN presence. Note its `player_id`.
4. Settings → Players → the new player. Identify the exact on/off
   `PlayerConfig` key exposed in the UI (the issue's repro used a generic
   on/off setting present on every player type — confirm the literal key
   name here before scripting step 12, since it's provider-shape-dependent
   and I have not verified it against this specific build).

### 3. Confirm the fix via the gateway

1. Through the `manightly_*` tool surface (confirm exact tool names via
   discovery against this new backend — likely a `config_set_player_value`-
   shaped call), set the identified on/off setting to the **string**
   `"false"`.
2. Read it back. Expect **off** — pre-fix this would have read back **on**
   (the exact bug: `bool("false")` → `True`).
3. Repeat for `"0"` and `"off"` — both should also read off.
4. (Negative control) Set it to `"true"`, confirm it reads on — proves the
   read-back isn't just defaulting to off regardless of input.
5. Record pass/fail. This is what goes back to Marvin Schenkel on
   music-assistant/support#6023.

### 4. Teardown — leave zero residue

1. Close the port-forward / browser tab.
2. Delete the throwaway app entirely rather than just re-suspending (this
   was always meant to be single-use):

   ```sh
   git rm -r kubernetes/apps/media/music-assistant-nightly \
             kubernetes/apps/mcp-system/music-assistant-nightly-mcp
   ```

   and remove the two index lines added to:
   - `kubernetes/apps/media/kustomization.yaml`
   - `kubernetes/apps/mcp-system/kustomization.yaml`

   Commit + push to `main`. Flux prunes: HelmRelease → pod/StatefulSet/
   Service/CiliumNetworkPolicy, the `ceph-block` PVC (→ RBD image deleted,
   `reclaimPolicy: Delete`), the ReferenceGrant, the HTTPRoute, both
   `music-assistant-nightly-mcp-*-egress` CNPs, and the
   `MCPServerRegistration`. Nothing Retain-policy'd, nothing left behind —
   same "one PR, Flux prunes everything" shape as the claude-runner revert
   pattern documented in `.agents/instructions/claude-runner-routing.md`.
3. Verify nothing's left:
   `kubectl -n media get all,pvc,referencegrant,cnp -l app.kubernetes.io/name=music-assistant-nightly`
   `kubectl -n mcp-system get httproute,cnp,mcpserverregistration -l app.kubernetes.io/name=music-assistant-nightly-mcp`
   both should return empty.
4. Delete the branch: `git branch -d claude/ma-nightly-6023-test` locally,
   delete the remote branch too if it was pushed.
5. Rob comments back on music-assistant/support#6023 with the pass/fail
   result, citing nightly tag `2.11.0.dev2026102203`.
