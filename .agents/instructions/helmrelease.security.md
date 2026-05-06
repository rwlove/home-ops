# HelmRelease security-context defaults

When scaffolding a **new** app-template HelmRelease (or hardening an
existing one), apply the defaults below unless you have a documented
reason to deviate. These are baseline pod-security guarantees — not
aspirational.

## Defaults

```yaml
spec:
  values:
    controllers:
      <app>:
        pod:
          securityContext:
            runAsNonRoot: true
            runAsUser: 1000
            runAsGroup: 1000
            fsGroup: 1000
            fsGroupChangePolicy: OnRootMismatch
        containers:
          app:
            securityContext:
              allowPrivilegeEscalation: false
              readOnlyRootFilesystem: true
              capabilities:
                drop:
                  - ALL
              seccompProfile:
                type: RuntimeDefault
```

## Why

- `runAsNonRoot: true` + non-zero `runAsUser/Group` — refuse to run as
  root even if the image's `USER` directive is missing or wrong.
- `readOnlyRootFilesystem: true` — turn the container's `/` read-only.
  Confines a compromised process to whatever volumes you've explicitly
  mounted, and surfaces sloppy "writes-to-rootfs-at-startup" behavior at
  scaffolding time instead of in prod.
- `allowPrivilegeEscalation: false` + `capabilities.drop: [ALL]` — block
  setuid/setgid binaries from gaining new caps; drop the kernel
  capability set Linux gives every container by default.
- `fsGroup` + `fsGroupChangePolicy: OnRootMismatch` — let the kubelet
  chown the volume root so the non-root pod can actually write to its
  PVCs, but only when the top-level isn't already correct (avoids
  multi-minute recursive chowns on large volumes).
- `seccompProfile.RuntimeDefault` — opt into the runtime's default
  seccomp filter (vs the unconfined default).

## When to deviate

`readOnlyRootFilesystem: true` is the field most likely to break an app.
Two patterns to handle that:

1. **App writes to a known scratch path.** Add a `tmpfs` `emptyDir`
   `persistence` block and mount it where the app expects to write:

   ```yaml
   persistence:
     tmpfs:
       type: emptyDir
       advancedMounts:
         <app>:
           app:
             - path: /tmp
               subPath: tmp
             - path: /var/cache
               subPath: cache
   ```

2. **App refuses to start under a read-only rootfs.** Disable per-container:

   ```yaml
   containers:
     app:
       securityContext:
         readOnlyRootFilesystem: false
   ```

   Document **why** in a comment next to the override (e.g. "slskd 0.25
   entrypoint runs `[ ! -w /app ]` self-check; see
   `project_slskd_readonly_rootfs.md`"). If the override is permanent,
   it belongs in memory, not just a comment.

`runAsUser/Group: 1000` is a *default*, not a rule — an app that needs
to read media via group `1112` (gonic) or a specific UID baked into the
image (linuxserver.io family) overrides per-controller. Pick the lowest
privilege that lets the app function; don't use root just to skip the
fsGroup chown.

## What this is NOT

- A retrofit-everything mandate. Existing HelmReleases that work and
  meet the cluster's actual threat model don't need to be churned just
  to match this template.
- A substitute for `defaultPodOptions`. The bjw-s common chart applies
  some defaults at the pod level — these container-level fields still
  need to be set because they aren't inherited.
