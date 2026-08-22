@AGENTS.md
@.agents/instructions/claude-runner-routing.md
@.agents/instructions/configmap.resources.instructions.md
@.agents/instructions/flux.sorting.instructions.md
@.agents/instructions/gpu-routing.md
@.agents/instructions/helmfile.sorting.instructions.md
@.agents/instructions/helmrelease.security.md
@.agents/instructions/kustomize.config.sorting.instructions.md
@.agents/instructions/schema.correction.md
@.agents/instructions/storage-class.instructions.md
@.agents/instructions/workarounds.md

# CLAUDE.md — home-ops

The repository guide, persona and prime directives, worktree-isolation rules and
data-classification tiers live in [`AGENTS.md`](AGENTS.md), imported above.
That file is the single source of truth and is shared with opencode, which reads
`AGENTS.md` and can read neither this file nor `@` imports.

The remaining imports above are the task-specific instructions that `AGENTS.md`
only indexes. Claude Code has the context budget to carry them always-loaded;
the in-cluster opencode agent (65,536 tokens) does not, and reads them on demand.

**Adding a new instruction file:** put it in `.agents/instructions/`, then either
inline it in `AGENTS.md` (if it applies to *every* task) or add it to the index
there plus an `@` import here (if it is task-specific).
