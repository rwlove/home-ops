# esphome-reconciler

A standing reconciler that keeps the 12 git-tracked ESPHome devices' firmware
**deployed-from-git**: it detects out-of-date devices, **compiles** each (a hard
precondition), **OTA-flashes** the out-of-date ones one at a time, verifies each
is back online, and emails a per-run result. It is the missing "deploy" half of
the GitOps loop — Renovate/hand PRs bump the pinned config refs, Flux bakes them
into the `esphome-devices` ConfigMap, but nothing flashes the devices. This
closes that gap.

## Execution path — scoped `kubectl exec`, not the dashboard API, never the broker

The devices sit on the IoT VLAN and are reachable **only** from `esphome-0`
(its Multus secondary NIC `esphome-iot-static`; that traffic bypasses Cilium).
So both compile and flash must run **from** `esphome-0`. The reconciler is a
tiny CronJob pod that opens a scoped `pods/exec` stream into `esphome-0` and
runs the **esphome CLI** there:

- `esphome compile /config/<file>.yaml` — the **hard gate** (non-zero exit
  aborts before any OTA). Reuses the warm `/cache` (`ESPHOME_BUILD_PATH`,
  `PLATFORMIO_CORE_DIR`) natively.
- `esphome upload /config/<file>.yaml --device <addr>` — the OTA, which leaves
  over `esphome-0`'s IoT NIC — the one path that reaches the device.

Chosen over driving the dashboard HTTP/WS command API because the CLI gives a
clean compile/flash **exit-code** gate (no WebSocket event-stream parsing), and
over the mcp-gateway because an unattended **mutating** loop must never sit
behind the broker (no per-tool authz —
`reference_mcp_broker_unsafe_for_unattended_loops`). The reconciler's only
cluster power is `exec` into the **single** `esphome-0` pod (`rbac.yaml`,
`resourceNames: [esphome-0]`).

## Out-of-date detection — content-hash + core-version-drift

The dashboard's `GET /devices` reports per-device `deployed_version` /
`current_version`, but these only track the ESPHome **core** version baked at
last compile. That catches a core/image bump (e.g. `pool-controller` at
`2026.9.0` vs dashboard `2026.9.1`) but **misses external-component `@tag`
bumps** — the common Renovate change (konnected/ratgdo show matching versions
despite stale pins). So detection is the union of:

1. **content-hash** — `sha256sum` of the rendered `/config/<file>.yaml`
   (post-envsubst, exactly what compiles) vs a baseline stored in
   `/config/.esphome-reconciler-state.json` (on the RWX config PVC). Catches
   any config change: tag bumps, hand edits.
2. **core-version-drift** — `deployed_version != current_version` from
   `/devices`. Catches an esphome core/image bump (no git file changed).

An **untracked** device (no baseline) that is *not* version-drifted is
baseline-seeded, **not** flashed — so adopting the reconciler does not reflash
healthy devices. The genuinely-stale backlog (konnected, ratgdo ×2) is
converged once via `FORCE_DEVICES`; `pool-controller` is caught automatically by
version-drift.

## Sequencing & rate limit

One device at a time: compile → OTA → **verify** the device's native-API port
(`:6053`) accepts a connection again (clean reboot, no boot-loop) before the
next. Any failure (compile, OTA, or verify timeout) **stops the run** — it never
cascades into the next device in an unknown state. `MAX_PER_RUN` (default 3)
caps each run; the rest defer to subsequent runs. A core image bump marks the
whole fleet out-of-date, so the cap + daily cadence drains it over several days
— never a concurrent mass reflash of garage doors / alarm panel / voice / pool.

## Notifications

One email per run via `notify-mcp` `/send` (recipient-locked to Rob),
problems/results-only: a run that flashed something, failed, or (in dry-run)
found candidates is reported; a no-op run sends nothing
(`feedback_digests_problems_only_no_allclear_roster`).

## Double OFF gate & bring-up

Ships OFF two ways (mirrors `ai/network-drift`): `suspend: true` **and**
`DRY_RUN=true`. Merging this flashes nothing.

1. **Safe dry-run test** (compiles candidates, proves the gate, no OTA):

   ```sh
   kubectl -n home create job --from=cronjob/esphome-reconciler esphome-reconcile-test
   kubectl -n home logs job/esphome-reconcile-test -f
   ```

   Today `pool-controller.yaml` appears as a candidate (version-drift) and is
   compiled. To dry-run the tag-bump backlog too, set `FORCE_DEVICES`
   (`kubectl -n home edit cronjob esphome-reconciler`) to
   `konnected-9d7cc0.yaml,ratgdo_primary-1bb40c.yaml,ratgdo_secondary-1bc561.yaml`
   before creating the job, then clear it.
2. **Go live:** set `DRY_RUN=false` (the one toggle), then `suspend=false`.
3. **One-time backlog convergence:** with `DRY_RUN=false`, set `FORCE_DEVICES`
   to the stale configs for a run (still capped), then clear it. Steady-state
   detection takes over.

## Tunables (CronJob env)

`MAX_PER_RUN` · `FORCE_DEVICES` · `DRY_RUN` · `COMPILE_TIMEOUT` ·
`UPLOAD_TIMEOUT` · `VERIFY_TIMEOUT` · `VERIFY_POLL`. Schedule is daily 03:30 ET
(overnight quiet window — an OTA briefly reboots the device).
