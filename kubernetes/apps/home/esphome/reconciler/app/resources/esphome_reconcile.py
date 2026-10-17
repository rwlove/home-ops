#!/usr/bin/env python3
"""ESPHome compile+OTA reconciler — keep device firmware deployed-from-git.

Detects out-of-date git-tracked ESPHome devices, COMPILES each (a hard
precondition), then OTA-flashes the out-of-date ones ONE AT A TIME, verifying
each is back online before the next. Problems/results-only email per run.

HARD design constraints (repo policy / memory), encoded here:
  * NO MCP broker. Compile+flash run via the esphome CLI INSIDE the esphome-0
    pod (scoped `pods/exec`), reusing the warm /cache. The OTA egress leaves
    esphome-0's Multus NIC on the IoT VLAN — the only path to the devices —
    exactly as the manual dashboard flow does. This process never touches the
    mcp-gateway. (reference_mcp_broker_unsafe_for_unattended_loops)
  * Compile is a HARD gate: a non-zero `esphome compile` aborts the run before
    any OTA; a firmware that did not compile green is never flashed.
  * Trigger = the dashboard's OWN device list (`GET /devices`) for core-version
    drift, PLUS a per-device content hash of the rendered /config file for
    config changes (Renovate `@tag` bumps, hand edits). The dashboard's
    deployed/current version only reflects the esphome CORE version baked at
    last compile, so it MISSES external-component tag bumps — hence the hash.
  * ONE device at a time; verify API (:6053) back up before the next. A core
    image bump marks the whole fleet out-of-date, so MAX_PER_RUN caps each run
    and the rest drain over subsequent runs — never a concurrent mass reflash.
  * notify-mcp email only; silent on a no-op run (problems/results only).
  * Double OFF gate: CronJob `suspend: true` AND `DRY_RUN=true`. DRY_RUN
    compiles candidates (proving the gate) but NEVER uploads and NEVER writes
    state. The single toggle that permits real OTA is DRY_RUN=false.

Env (all optional except where noted; defaults shown):
  NAMESPACE=home  POD=esphome-0  CONTAINER=app
  CONFIG_DIR=/config  STATE_FILE=/config/.esphome-reconciler-state.json
  DASHBOARD=http://localhost:6052   (fetched via exec, inside the pod)
  MAX_PER_RUN=3
  FORCE_DEVICES=""   (comma-sep config filenames, or "ALL" — bypass detection)
  DRY_RUN=true       (OFF gate; "false" permits OTA)
  COMPILE_TIMEOUT=600  UPLOAD_TIMEOUT=300  VERIFY_TIMEOUT=240  VERIFY_POLL=10
  DEVICE_API_PORT=6053
  NOTIFY_URL=http://notify-mcp.mcp-system:8060/send  AGENT=smart-home-operator

Exit codes: 0 on a clean run OR a device failure that was emailed (the email is
the actionable signal; avoids double-paging via KubeJobFailed). Non-zero ONLY on
an infrastructure failure (cannot init the client, exec, or read /devices).
"""
import base64
import datetime
import json
import os
import sys
import time
import urllib.request

from kubernetes import client, config
from kubernetes.stream import stream

NAMESPACE = os.environ.get("NAMESPACE", "home")
POD = os.environ.get("POD", "esphome-0")
CONTAINER = os.environ.get("CONTAINER", "app")
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config")
STATE_FILE = os.environ.get("STATE_FILE", "/config/.esphome-reconciler-state.json")
DASHBOARD = os.environ.get("DASHBOARD", "http://localhost:6052")
MAX_PER_RUN = int(os.environ.get("MAX_PER_RUN", "3"))
FORCE = [x.strip() for x in os.environ.get("FORCE_DEVICES", "").split(",") if x.strip()]
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"
COMPILE_TIMEOUT = int(os.environ.get("COMPILE_TIMEOUT", "600"))
UPLOAD_TIMEOUT = int(os.environ.get("UPLOAD_TIMEOUT", "300"))
VERIFY_TIMEOUT = int(os.environ.get("VERIFY_TIMEOUT", "240"))
VERIFY_POLL = int(os.environ.get("VERIFY_POLL", "10"))
DEVICE_API_PORT = os.environ.get("DEVICE_API_PORT", "6053")
NOTIFY_URL = os.environ.get("NOTIFY_URL", "http://notify-mcp.mcp-system:8060/send")
AGENT = os.environ.get("AGENT", "smart-home-operator")

_core = None


def log(m):
    print(m, flush=True)


def _die(msg, rc=2):
    """Infrastructure failure: surface via a non-zero exit (KubeJobFailed)."""
    log("FATAL: %s" % msg)
    sys.exit(rc)


def exec_pod(cmd, timeout=60):
    """Run `sh -c <cmd>` in esphome-0. Returns (rc, stdout, stderr).

    rc comes from a sentinel echoed after the command (authoritative across
    client versions); a wall-clock timeout returns rc=124 like `timeout(1)`.
    """
    sentinel = "__ESPHOME_RC__:"
    wrapped = "%s; echo \"%s$?\"" % (cmd, sentinel)
    resp = stream(
        _core.connect_get_namespaced_pod_exec, POD, NAMESPACE,
        container=CONTAINER, command=["/bin/sh", "-c", wrapped],
        stderr=True, stdin=False, stdout=True, tty=False,
        _preload_content=False,
    )
    out, err = [], []
    start = time.time()
    rc = None
    try:
        while resp.is_open():
            if time.time() - start > timeout:
                rc = 124
                break
            resp.update(timeout=1)
            if resp.peek_stdout():
                out.append(resp.read_stdout())
            if resp.peek_stderr():
                err.append(resp.read_stderr())
    finally:
        resp.close()
    sout = "".join(out)
    serr = "".join(err)
    if rc == 124:
        return 124, sout, serr + "\n[reconciler] exec timed out after %ss" % timeout
    for line in sout.splitlines():
        if line.startswith(sentinel):
            try:
                rc = int(line[len(sentinel):].strip())
            except ValueError:
                rc = 1
    sout = "\n".join(l for l in sout.splitlines() if not l.startswith(sentinel))
    if rc is None:
        rc = resp.returncode if resp.returncode is not None else 1
    return rc, sout, serr


def fetch_devices():
    """Dashboard /devices (fetched from inside the pod on localhost). Returns
    {configuration_filename: {current, deployed, address, platform, name}}."""
    rc, out, err = exec_pod(
        "curl -s --max-time 20 %s/devices" % DASHBOARD, timeout=30)
    if rc != 0 or not out.strip():
        _die("cannot read dashboard /devices (rc=%s err=%s)" % (rc, err.strip()))
    try:
        data = json.loads(out)
    except Exception as e:  # noqa: BLE001
        _die("dashboard /devices not JSON: %r" % e)
    devs = {}
    for e in data.get("configured", []):
        fn = e.get("configuration")
        if not fn:
            continue
        devs[fn] = {
            "current": e.get("current_version"),
            "deployed": e.get("deployed_version"),
            "address": e.get("address"),
            "platform": e.get("target_platform"),
            "name": e.get("name"),
        }
    return devs


def config_hash(fn):
    rc, out, _ = exec_pod("sha256sum %s/%s" % (CONFIG_DIR, fn), timeout=30)
    if rc != 0:
        return None
    return out.strip().split()[0] if out.strip() else None


def load_state():
    rc, out, _ = exec_pod("cat %s 2>/dev/null || echo '{}'" % STATE_FILE, timeout=30)
    try:
        return json.loads(out) if out.strip() else {}
    except Exception:  # noqa: BLE001
        log("WARN: state file unparseable; treating as empty")
        return {}


def save_state(state):
    blob = base64.b64encode(json.dumps(state, indent=2).encode()).decode()
    cmd = ("echo '%s' | python3 -c "
           "\"import sys,base64;open('%s','wb').write(base64.b64decode(sys.stdin.read()))\""
           % (blob, STATE_FILE))
    rc, _, err = exec_pod(cmd, timeout=30)
    if rc != 0:
        log("WARN: could not persist state (rc=%s err=%s)" % (rc, err.strip()))


def verify_online(address):
    """Poll the device native-API port from inside esphome-0 (over the Multus
    IoT NIC) until it accepts a connection, i.e. the device rebooted cleanly."""
    probe = ("python3 -c \"import socket;socket.setdefaulttimeout(4);"
             "socket.create_connection(('%s',%s));print('UP')\""
             % (address, DEVICE_API_PORT))
    deadline = time.time() + VERIFY_TIMEOUT
    while time.time() < deadline:
        rc, out, _ = exec_pod(probe, timeout=10)
        if rc == 0 and "UP" in out:
            return True
        time.sleep(VERIFY_POLL)
    return False


def notify(subject, body):
    data = json.dumps({"subject": subject, "body": body, "agent": AGENT}).encode()
    req = urllib.request.Request(
        NOTIFY_URL, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.getcode()


def classify(devs, state):
    """Return (candidates, seeded). candidate = (fn, reason). `seeded` = untracked
    devices whose baseline hash we recorded this run (no flash)."""
    candidates, seeded = [], []
    forced_all = "ALL" in FORCE
    for fn in sorted(devs):
        d = devs[fn]
        cur = config_hash(fn)
        if cur is None:
            log("WARN: cannot hash %s — skipping" % fn)
            continue
        drift = (d["deployed"] and d["current"] and d["deployed"] != d["current"])
        prev = state.get(fn)
        if forced_all or fn in FORCE:
            candidates.append((fn, "forced"))
        elif prev is None:
            # Untracked and not version-drifted: record baseline, do NOT flash.
            # (Avoids reflashing healthy devices on adoption; genuinely-stale
            # backlog is handled via FORCE_DEVICES / version drift.)
            if drift:
                candidates.append((fn, "core-version-drift %s->%s"
                                   % (d["deployed"], d["current"])))
            else:
                state[fn] = {"hash": cur}
                seeded.append(fn)
        elif prev.get("hash") != cur:
            candidates.append((fn, "config-changed"))
        elif drift:
            candidates.append((fn, "core-version-drift %s->%s"
                               % (d["deployed"], d["current"])))
    return candidates, seeded


def flash_one(fn, devs, state):
    """Compile (hard gate) then OTA one device; verify online. Returns a result
    dict. Raises RuntimeError on a hard failure (caller stops the run)."""
    addr = devs[fn]["address"]
    log("=== %s (%s) — compiling ===" % (fn, addr))
    rc, out, err = exec_pod(
        "esphome compile %s/%s" % (CONFIG_DIR, fn), timeout=COMPILE_TIMEOUT)
    tail = (out or err).splitlines()[-8:]
    if rc != 0:
        raise RuntimeError("compile FAILED (rc=%s) for %s:\n%s"
                           % (rc, fn, "\n".join(tail)))
    log("compile OK: %s" % fn)
    if DRY_RUN:
        log("DRY_RUN: skipping OTA for %s (no upload, no state write)" % fn)
        return {"fn": fn, "status": "dry-run-compiled"}
    log("=== %s — OTA upload ===" % fn)
    rc, out, err = exec_pod(
        "esphome upload %s/%s --device %s" % (CONFIG_DIR, fn, addr),
        timeout=UPLOAD_TIMEOUT)
    if rc != 0:
        tail = (out or err).splitlines()[-8:]
        raise RuntimeError("OTA FAILED (rc=%s) for %s:\n%s"
                           % (rc, fn, "\n".join(tail)))
    log("OTA OK: %s — verifying online" % fn)
    if not verify_online(addr):
        raise RuntimeError("%s did not come back on :%s within %ss after OTA "
                           "(possible boot-loop — STOPPING run)"
                           % (fn, DEVICE_API_PORT, VERIFY_TIMEOUT))
    cur = config_hash(fn)
    if cur:
        state[fn] = {"hash": cur}
        save_state(state)
    log("VERIFIED online: %s" % fn)
    return {"fn": fn, "status": "flashed"}


def main():
    global _core
    try:
        config.load_incluster_config()
        _core = client.CoreV1Api()
    except Exception as e:  # noqa: BLE001
        _die("cannot init in-cluster k8s client: %r" % e)

    mode = "DRY-RUN" if DRY_RUN else "LIVE"
    log("esphome-reconciler start (%s, max_per_run=%d, force=%s)"
        % (mode, MAX_PER_RUN, FORCE or "-"))

    devs = fetch_devices()
    log("dashboard reports %d configured devices" % len(devs))
    state = load_state()

    candidates, seeded = classify(devs, state)
    if seeded:
        log("baseline-seeded (untracked, not flashed): %s" % ", ".join(seeded))
        save_state(state)

    if not candidates:
        log("no out-of-date devices — silent no-op run")
        log("RECONCILE_RESULT=noop")
        return 0

    batch = candidates[:MAX_PER_RUN]
    deferred = candidates[MAX_PER_RUN:]
    log("candidates: %s" % ", ".join("%s(%s)" % (f, r) for f, r in candidates))
    log("this run: %s%s" % (", ".join(f for f, _ in batch),
                            " | deferred: %s" % ", ".join(f for f, _ in deferred)
                            if deferred else ""))

    results, failure = [], None
    for fn, reason in batch:
        try:
            res = flash_one(fn, devs, state)
            res["reason"] = reason
            results.append(res)
        except RuntimeError as e:
            log("STOP: %s" % e)
            failure = (fn, str(e))
            break  # do not cascade into the next device in an unknown state

    # ---- report (problems/results only) ----
    flashed = [r["fn"] for r in results if r["status"] == "flashed"]
    compiled = [r["fn"] for r in results if r["status"] == "dry-run-compiled"]
    send = bool(flashed) or failure is not None or (DRY_RUN and bool(batch))
    if not send:
        log("RECONCILE_RESULT=noop-no-action")
        return 0

    day = datetime.datetime.now().strftime("%Y-%m-%d %H:%M %Z")
    lines = ["**ESPHome reconciler** — %s (%s)" % (day, mode), ""]
    if flashed:
        lines.append("**Flashed & verified online:**")
        lines += ["- `%s` — %s" % (r["fn"], r["reason"]) for r in results
                  if r["status"] == "flashed"]
    if compiled:
        lines.append("**Dry-run compiled OK (not flashed):**")
        lines += ["- `%s` — %s" % (r["fn"], r["reason"]) for r in results
                  if r["status"] == "dry-run-compiled"]
    if failure:
        lines += ["", "**FAILED (run stopped):**", "- `%s`" % failure[0],
                  "```", failure[1][:600], "```"]
    if deferred:
        lines += ["", "**Deferred to next run (per-run cap %d):**" % MAX_PER_RUN]
        lines += ["- `%s` — %s" % (f, r) for f, r in deferred]

    subject = "esphome-reconciler: %d flashed%s%s" % (
        len(flashed),
        ", %d compiled" % len(compiled) if compiled else "",
        ", 1 FAILED" if failure else "")
    body = "\n".join(lines)
    try:
        code = notify(subject, body)
        log("emailed via notify-mcp (HTTP %s)" % code)
    except Exception as e:  # noqa: BLE001
        log("WARN: notify-mcp delivery failed: %r" % e)
        # analysis is in the log; do not hard-fail on a delivery hiccup.
    log("RECONCILE_RESULT=flashed=%d compiled=%d failed=%d deferred=%d"
        % (len(flashed), len(compiled), 1 if failure else 0, len(deferred)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
