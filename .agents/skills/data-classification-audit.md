---
name: data-classification-audit
description: Audit a PR diff and description for data-classification violations — restricted lexicon (arr stack, stash, secrets, hostnames), and ensure no restricted content reaches external surfaces.
last_verified: 2026-05-25
---

# Data classification audit

One-shot check that a PR diff + description is safe to publish per
`.agents/instructions/data-classification.md` and HOMELAB-SPEC Layer 5.
Run as the final step in pre-submit (checklist item #7) before pushing.

## When to use

- Before opening any PR that touches `kubernetes/apps/media/`,
  `kubernetes/apps/security/`, docs, READMEs, or memory entries.
- Whenever the PR description mentions app names, hostnames, or
  integration names that might fall under restricted patterns.
- As a pre-push sanity check on any PR with an external-facing
  artifact (docs site, README, mdBook chapter).

## Check 1 — Restricted lexicon in diff

```bash
# Run from repo root against the PR diff vs main.
# Fails if any restricted term appears in changed lines of docs/,
# README files, or .agents/ (but not kubernetes/apps/ — app names in
# manifests are fine; they're not external artifacts).
git diff origin/main...HEAD -- 'docs/' 'README*' '.agents/' \
  | grep '^+' \
  | grep -iE '\b(radarr|sonarr|lidarr|readarr|prowlarr|bazarr|jackett|nzbget|stash)\b' \
  && echo "FAIL: restricted app name in external artifact" \
  || echo "OK: no restricted lexicon in external-artifact diff"
```

## Check 2 — Restricted lexicon in PR description

If the PR description has been drafted, grep it:

```bash
# Paste the PR body into /tmp/pr-body.txt first.
grep -iE '\b(radarr|sonarr|lidarr|readarr|prowlarr|bazarr|jackett|nzbget|stash)\b' \
  /tmp/pr-body.txt \
  && echo "FAIL: restricted app name in PR description" \
  || echo "OK: PR description clean"
```

## Check 3 — Hostnames and internal IPs in external artifacts

```bash
git diff origin/main...HEAD -- 'docs/' 'README*' \
  | grep '^+' \
  | grep -E '([0-9]{1,3}\.){3}[0-9]{1,3}|\.local\b|\.internal\b|brain\b|beast\b|worker[0-9]' \
  && echo "WARN: possible internal hostname/IP in external artifact — review manually" \
  || echo "OK: no obvious internal hostnames in external artifact diff"
```

## Check 4 — Secrets in diff

```bash
# gitleaks covers this at commit time, but a belt-and-suspenders check
# before push is worthwhile for ExternalSecret resolved values that
# might have been accidentally inlined.
git diff origin/main...HEAD \
  | grep '^+' \
  | grep -iE '(password|secret|token|apikey|api_key)\s*[:=]\s*["\047]?[A-Za-z0-9+/]{16,}' \
  && echo "FAIL: possible credential in diff — review immediately" \
  || echo "OK: no obvious credentials in diff"
```

## Interpreting results

| Output | Action |
|---|---|
| `FAIL: restricted app name in external artifact` | Remove or replace with "the media stack" / "the download stack" |
| `FAIL: restricted app name in PR description` | Rewrite using category names only |
| `WARN: possible internal hostname` | Review — role descriptions (`the gateway`, `the storage node`) are fine; raw names and IPs are not |
| `FAIL: possible credential in diff` | Stop. Do not push. Find and remove the credential. |

## What this is NOT

- Not a substitute for `gitleaks` / `detect-secrets` pre-commit hooks — those catch
  credentials at commit time; this catches classification at publish time.
- Not exhaustive — it checks the known restricted lexicon. New restricted
  terms should be added to the grep patterns here and in
  `.agents/instructions/data-classification.md`.
- Not a code-content filter — app names inside `kubernetes/apps/` YAML
  are intentionally excluded (they're not external artifacts).
