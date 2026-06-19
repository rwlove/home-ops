#!/bin/sh
# Remove a Claude Code worktree created by tools/claude-worktree.sh.
#
# Usage:
#   tools/claude-worktree-cleanup.sh <task-slug> [--force]
#
# Without --force, refuses if:
#   - the working tree has uncommitted changes, OR
#   - the branch has not been merged into origin/main.
#
# With --force, skips both checks (use after a PR is merged or when
# discarding an abandoned task).
set -eu

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WT_BASE="$HOME/workspace/claude-workspace/home-ops.worktrees"

usage() {
  printf 'Usage: %s <task-slug> [--force]\n' "$0" >&2
  exit 1
}

[ $# -ge 1 ] || usage

slug="$1"
force=0
if [ "${2:-}" = "--force" ]; then
  force=1
fi

WT="$WT_BASE/$slug"
BRANCH="claude/$slug"

if [ ! -d "$WT" ]; then
  printf 'Worktree not found: %s\n' "$WT" >&2
  exit 1
fi

if [ "$force" -eq 0 ]; then
  # Refuse if the working tree is dirty.
  if ! git -C "$WT" diff --quiet HEAD 2>/dev/null; then
    printf 'ERROR: worktree has uncommitted changes. Commit, stash, or use --force.\n' >&2
    exit 1
  fi

  # Refuse if the branch hasn't landed in origin/main.
  git -C "$REPO_ROOT" fetch --quiet origin main
  branch_sha=$(git -C "$REPO_ROOT" rev-parse "$BRANCH" 2>/dev/null || true)
  if [ -z "$branch_sha" ]; then
    printf 'ERROR: branch %s not found locally. Use --force to remove orphaned worktree.\n' "$BRANCH" >&2
    exit 1
  fi
  if ! git -C "$REPO_ROOT" merge-base --is-ancestor "$branch_sha" origin/main; then
    printf 'ERROR: branch %s has not been merged into origin/main.\n' "$BRANCH" >&2
    printf '       Push your PR and merge it first, or use --force to discard.\n' >&2
    exit 1
  fi
fi

printf 'Removing worktree %s...\n' "$WT"
git -C "$REPO_ROOT" worktree remove "$WT" ${force:+--force} 2>/dev/null || \
  git -C "$REPO_ROOT" worktree remove --force "$WT" 2>/dev/null || true

# Delete the local branch only if it still exists.
if git -C "$REPO_ROOT" rev-parse --verify "$BRANCH" >/dev/null 2>&1; then
  if [ "$force" -eq 1 ]; then
    git -C "$REPO_ROOT" branch -D "$BRANCH"
  else
    git -C "$REPO_ROOT" branch -d "$BRANCH"
  fi
  printf 'Branch %s deleted.\n' "$BRANCH"
fi

printf 'Done.\n'
