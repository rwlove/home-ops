# Data classification

Every narrative artifact this repo produces — PR descriptions, README
content, mdBook chapters, memory entries, prompts sent to remote
models, summaries, blog drafts — carries an implicit data tier. Pick
the tier before you emit. Per HOMELAB-SPEC Layer 5.

## Tiers

- **Public** — May appear in published docs, summaries, external-facing
  artifacts (repo READMEs, mdBook output rendered at
  <https://rwlove.github.io/home-ops/>, anything Anthropic or other
  external readers see).
- **Internal** — Cluster internals, runbooks, decisions. Vault and repo.
  Not external.
- **Restricted** — Secrets, the arr stack and stash, anything Rob has
  flagged restricted. Never summarized, never indexed for retrieval,
  never emitted to remote models.

## Restricted patterns in this repo

- Secrets — any 1Password reference values, key material, API tokens,
  OIDC client secrets, htpasswd content. If it's behind an
  `ExternalSecret`, the resolved value is restricted.
- **Do not name the arr stack or stash in PR descriptions, READMEs,
  mdBook output, or any artifact a third party could see** (per
  HOMELAB-SPEC Layer 2 #2). They exist in `kubernetes/apps/media/`
  but narrative artifacts treat them as "the media stack."
- **Media file names are restricted** (per HOMELAB-SPEC Layer 2 #10).
  Track / album / artist filenames and library paths never appear in
  commit messages, PR titles/descriptions, public code, code comments,
  `docs/`, or externally-visible metadata. Describe the *issue*
  generically — "a library track", "the affected file" — never the
  name. This applies even when debugging a specific file (e.g. a bad
  tag on one track): reference it by an opaque ID (gonic track id,
  DB row id) in public artifacts, not its filename. MusicBrainz IDs
  and similar opaque public identifiers are fine; the human-readable
  name is not.
- Anything under `kubernetes/apps/security/` — this namespace exists
  for hardening / audit work; assume restricted unless proven
  otherwise.
- Specific hostnames, internal IPs, MAC addresses unless required for
  the technical content. Prefer roles (`the gateway`, `the storage
  node`) over names in external artifacts.

## When to check

- Before opening a PR — review the title, body, and any new doc
  content.
- Before writing or updating anything in `docs/src/`.
- Before sending content to a remote model (Anthropic API, etc.).
- Before writing memory entries that might be surfaced elsewhere
  (vault, summaries, indexed retrieval).
- Before producing any external artifact (LinkedIn post, blog draft,
  conference abstract).

## How to redact

- Replace specific names with category descriptors ("the media stack"
  not the actual app names).
- Replace internal hostnames with `<hostname>` or the role (`the
  gateway`, `the storage node`).
- Replace media file names with a generic descriptor (`a library
  track`) or an opaque ID (`gonic tr-<id>`) — never the
  artist/album/track filename or library path.
- For secrets: do not redact — refuse to emit at all. If a secret has
  to appear in narrative, you're producing the wrong artifact.

## What this is NOT

- Not a substitute for ExternalSecrets. That's a separate layer — don't
  commit plaintext secrets to Git regardless of tier.
- Not a code-review rule for inline credentials — gitleaks + pre-commit
  hooks catch those, and they apply to every commit regardless of
  classification.
- This is about **narrative artifacts** — descriptions, summaries,
  prose — that humans or LLMs produce *about* the cluster. The cluster
  manifests themselves follow their own (separate) rules.
