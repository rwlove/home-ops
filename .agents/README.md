# `.agents/` — AI assistant resources for home-ops

Two kinds of files live here. Both are meant for AI coding assistants
(Claude Code, Copilot, Cursor, etc.) working in this repo.

## `instructions/` — auto-loaded context

These files are imported into `CLAUDE.md` via `@.agents/instructions/...`
and are therefore always loaded as context for every Claude Code
session. Keep them short, factual, and actionable — they're paying for
context window space on every turn.

| File | What it does |
|------|--------------|
| `configmap.resources.instructions.md` | ConfigMap source files live under `app/resources/`; file names match the in-container basename. |
| `data-classification.md` | Public / Internal / Restricted tiers for narrative artifacts (PR text, docs, summaries, prompts to remote models). |
| `flux.sorting.instructions.md` | Field ordering for Flux-managed YAML (Kustomization, dependsOn, sourceRef). |
| `gpu-routing.md` | Pointer to langgraph-agents' canonical `hardware-routing.md` plus home-ops-local GPU facts (P40, GB10, runtime split). |
| `helmfile.sorting.instructions.md` | Field ordering for HelmRelease YAML, especially app-template-based releases. |
| `helmrelease.security.md` | Pod-securityContext defaults (runAsNonRoot, readOnlyRootFilesystem, etc.) for new HelmReleases. |
| `kustomize.config.sorting.instructions.md` | Field ordering for kustomize `kustomization.yaml` files. |
| `persona.md` | Repo-specific persona — role framing, tone, decision bias. |
| `schema.correction.md` | apiVersion + kind → `# yaml-language-server: $schema=…` mappings used in this repo. |
| `storage-class.instructions.md` | When to pick Rook/Ceph vs Longhorn vs Garage vs direct NFS. |
| `workarounds.md` | `# workaround:` annotation format + `workaround` GitHub label convention; pairs with the upstream-watcher skill. |

When you add a new instructions file, also add an `@`-import line at
the top of `CLAUDE.md` so it gets loaded.

## `skills/` — invokable on demand

These are not auto-loaded. Each is a self-contained workflow you can
invoke explicitly (e.g. "use the add-app skill to scaffold X"). They
have YAML frontmatter (`name`, `description`) so AI clients that index
skills can discover them.

| Skill | What it does |
|-------|--------------|
| `add-app.md` | Scaffold a generic app-template HelmRelease application. |
| `add-cnpg-cluster.md` | Scaffold a CNPG postgres cluster with Garage-backed barman backups. |
| `add-mcp-server.md` | Scaffold an MCP server under `mcp-system/` with the sidecar `MCPServerRegistration`. |
| `dependency-mapper.md` | Build and validate the Flux Kustomization dependency graph. |
| `expose-app.md` | Attach an HTTPRoute to a per-app Gateway listener with shim-managed TLS. |
| `flux-suspend.md` | Document the suspend / unsuspend workflow and the `disable-<app>` commit pattern. |
| `historian.md` | Produce a weekly summary of this repo's work, written to the vault. |
| `pre-submit.md` | Author-time checklist gating agent-authored PRs before push. |
| `pr-review.md` | Apply the repo's PR review standards to a Renovate or manual PR. |
| `upstream-watcher.md` | Re-check `workaround`-labeled issues against upstream; open removal PRs when upstream catches up. |

## Conventions

- All YAML files in `kubernetes/` should follow the sorting rules in
  `instructions/`. Run them mentally before writing.
- All YAML files outside `resources/` directories should have a schema
  comment on line 2 — see `schema.correction.md`.
- Repo-specific facts (cluster entry point, sourceRef name, app-template
  Component, etc.) are documented in
  `skills/dependency-mapper.md` under "Repository-Specific Context".
