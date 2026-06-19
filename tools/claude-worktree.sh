#!/bin/sh
# Bootstrap a git worktree for a concurrent Claude Code session.
#
# Each Claude session working on this repo should run in its own worktree
# so that file changes in one session don't appear in another's git status.
#
# Usage:
#   tools/claude-worktree.sh <task-slug>
#
# Creates:
#   ~/workspace/claude-workspace/home-ops.worktrees/<task-slug>/
#   branch: claude/<task-slug>
#
# Idempotent: re-running with the same slug prints the existing path and exits 0.
#
# After creation, start your Claude session there:
#   cd ~/workspace/claude-workspace/home-ops.worktrees/<task-slug>
#   claude
#
# When done, clean up with:
#   tools/claude-worktree-cleanup.sh <task-slug>
set -eu

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WT_BASE="$HOME/workspace/claude-workspace/home-ops.worktrees"

usage() {
  printf 'Usage: %s <task-slug>\n' "$0" >&2
  printf '  task-slug: lowercase letters, digits, hyphens (e.g. fix-storage-weekly)\n' >&2
  exit 1
}

[ $# -eq 1 ] || usage

slug="$1"

# Validate: lowercase letters, digits, hyphens; must start with letter or digit.
case "$slug" in
  *[!a-z0-9-]*) printf 'ERROR: slug must contain only lowercase letters, digits, and hyphens\n' >&2; exit 1 ;;
  -*)           printf 'ERROR: slug must start with a letter or digit\n' >&2; exit 1 ;;
esac

WT="$WT_BASE/$slug"
BRANCH="claude/$slug"

if [ -d "$WT" ]; then
  current_branch=$(git -C "$WT" symbolic-ref --short HEAD 2>/dev/null || echo "(detached)")
  printf 'Worktree already exists:\n'
  printf '  path:   %s\n' "$WT"
  printf '  branch: %s\n' "$current_branch"
  exit 0
fi

mkdir -p "$WT_BASE"

printf 'Fetching origin/main...\n'
git -C "$REPO_ROOT" fetch --quiet origin main

printf 'Creating worktree %s on branch %s...\n' "$WT" "$BRANCH"
git -C "$REPO_ROOT" worktree add -b "$BRANCH" "$WT" origin/main

# Symlink both hooks into the new worktree.  git worktree stores its
# per-worktree git dir under .git/worktrees/<slug>/ in the main repo;
# the hooks subdir lives there.
WT_GIT="$REPO_ROOT/.git/worktrees/$slug"
if [ -d "$WT_GIT" ]; then
  mkdir -p "$WT_GIT/hooks"
  ln -sf "$REPO_ROOT/tools/git-hooks/pre-commit" "$WT_GIT/hooks/pre-commit"
  ln -sf "$REPO_ROOT/tools/git-hooks/pre-push"   "$WT_GIT/hooks/pre-push"
  printf 'Hooks linked into worktree git dir.\n'
else
  printf 'NOTE: could not locate worktree git dir at %s — link hooks manually:\n' "$WT_GIT" >&2
  printf '  ln -sf %s/tools/git-hooks/pre-commit <worktree-git-dir>/hooks/pre-commit\n' "$REPO_ROOT" >&2
  printf '  ln -sf %s/tools/git-hooks/pre-push   <worktree-git-dir>/hooks/pre-push\n'   "$REPO_ROOT" >&2
fi

printf '\nWorktree ready:\n'
printf '  cd %s\n' "$WT"
printf '  branch: %s\n' "$BRANCH"
