# git hooks

Hooks in this directory enforce repo-level invariants at commit and push time.

## Install (primary checkout)

```sh
ln -sf ../../tools/git-hooks/pre-commit .git/hooks/pre-commit
ln -sf ../../tools/git-hooks/pre-push   .git/hooks/pre-push
```

For worktrees created by `tools/claude-worktree.sh`, both hooks are linked
automatically.

## Hooks

| Hook | What it checks |
|------|----------------|
| `pre-commit` | Blocks commits that introduce literal credentials into staged YAML files. |
| `pre-push` | Blocks pushes from branches behind `origin/main`; run `git rebase origin/main` to fix. |
