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
| `flux.sorting.instructions.md` | Field ordering for Flux-managed YAML (Kustomization, dependsOn, sourceRef). |
| `helmfile.sorting.instructions.md` | Field ordering for HelmRelease YAML, especially app-template-based releases. |
| `kustomize.config.sorting.instructions.md` | Field ordering for kustomize `kustomization.yaml` files. |
| `schema.correction.md` | apiVersion + kind → `# yaml-language-server: $schema=…` mappings used in this repo. |
| `storage-class.instructions.md` | When to pick Rook/Ceph vs Longhorn vs Garage for new PVCs. |

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
| `flux-suspend.md` | Document the suspend / unsuspend workflow and the `disable-<app>` commit pattern. |
| `pr-review.md` | Apply the repo's PR review standards to a Renovate or manual PR. |

## Conventions

- All YAML files in `kubernetes/` should follow the sorting rules in
  `instructions/`. Run them mentally before writing.
- All YAML files outside `resources/` directories should have a schema
  comment on line 2 — see `schema.correction.md`.
- Repo-specific facts (cluster entry point, sourceRef name, app-template
  Component, etc.) are documented in
  `skills/dependency-mapper.md` under "Repository-Specific Context".
