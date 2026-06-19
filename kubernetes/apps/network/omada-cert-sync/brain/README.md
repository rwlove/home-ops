# omada-cert-sync (brain-side)

Pulls the cluster's Let's Encrypt wildcard cert (`*.thesteamedcrab.com`,
secret `thesteamedcrab-com-tls` in the `network` namespace) onto brain's
Omada controller and restarts Omada **only when the cert changed**. This
gives the guest captive portal a publicly-trusted cert by hostname, which
modern iOS/Android captive clients require — they render a blank page on
the old IP-based self-signed cert.

These files are **not Flux-managed**. They live on brain, a piece of core
network infrastructure (HOMELAB-SPEC Layer 2 #9 — human-installed, not
agent-touched). They are checked in for review and history only.

## How it stays automatic

`cert-manager` renews the wildcard ~30 days before its 90-day expiry and
rewrites the Secret. The systemd timer below fires daily; on the renewal
day it sees a new fingerprint, rebuilds the keystore, and restarts Omada.
Every other day it is a no-op. **No human action on rotation.** The only
manual step is this one-time install.

## One-time install (run on brain as root)

The cluster side (ServiceAccount `omada-cert-reader`, its Role, and the
long-lived token Secret `omada-cert-reader-token`) is applied by Flux from
`../app`. Confirm it exists before installing here.

1. Pull the token + CA from the cluster Secret (run from a machine with
   cluster kubectl, e.g. your workstation):

   ```bash
   kubectl -n network get secret omada-cert-reader-token \
     -o jsonpath='{.data.token}' | base64 -d > token
   kubectl -n network get secret omada-cert-reader-token \
     -o jsonpath='{.data.ca\.crt}' | base64 -d > ca.crt
   ```

2. Place config on brain (root-only):

   ```bash
   install -d -m 700 /etc/omada-cert-sync
   install -m 600 token  /etc/omada-cert-sync/token
   install -m 644 ca.crt /etc/omada-cert-sync/ca.crt
   shred -u token ca.crt   # don't leave the token lying around
   ```

3. Install the script + units:

   ```bash
   install -m 755 omada-cert-sync.sh /usr/local/bin/omada-cert-sync.sh
   install -m 644 omada-cert-sync.service /etc/systemd/system/
   install -m 644 omada-cert-sync.timer   /etc/systemd/system/
   systemctl daemon-reload
   ```

4. First run (manual, verifies the whole path end-to-end). This WILL
   restart Omada if the cert differs from the current self-signed one —
   expect a ~30-60s portal/controller blip:

   ```bash
   /usr/local/bin/omada-cert-sync.sh
   ```

5. Verify Omada now serves the LE cert on the portal port:

   ```bash
   echo | openssl s_client -connect 10.10.30.1:8843 -servername guest-portal.thesteamedcrab.com 2>/dev/null \
     | openssl x509 -noout -issuer -subject
   # issuer should be Let's Encrypt; subject CN *.thesteamedcrab.com
   ```

6. Enable the timer:

   ```bash
   systemctl enable --now omada-cert-sync.timer
   systemctl list-timers omada-cert-sync.timer
   ```

## Safety properties

- **Never restarts Omada without a valid new cert.** Any failure — cluster
  unreachable, token expired, malformed cert/key — logs and exits 0,
  leaving the existing keystore in place.
- **Backup on every change.** The prior keystore is copied to
  `eap.keystore.bak.<timestamp>` before the swap.
- **Least privilege.** The SA token can `get` exactly one Secret
  (`thesteamedcrab-com-tls`) in `network` — nothing else.

## Do NOT enable the controller's native HTTPS Certificate feature

Leave Omada's built-in **HTTPS Certificate** import (Settings → Controller)
**disabled**. Verified on controller v6.2.0.17: with that feature off
(`enable:false`), the controller serves directly from
`eap.keystore` on disk — the live cert on the management port (8043) and
the portal port (8843) both match the on-disk keystore fingerprint
exactly. That is *why* this file-swap approach works.

If someone toggles the native feature on, the controller starts managing
the keystore itself and will fight this script's swap. The two
mechanisms are mutually exclusive — keep the UI feature off and let the
timer own the keystore.

## Token note

The SA token is long-lived (a `kubernetes.io/service-account-token`
Secret), because brain is off-cluster and cannot use a rotated projected
token without renewal tooling — which would defeat "nothing manual." If
the token is ever revoked, re-run step 1–2.

## Logs

```bash
journalctl -t omada-cert-sync --since "7 days ago"
```
