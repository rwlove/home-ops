#!/usr/bin/env bash
# Pre-push drift check: simulate merge with origin/main, run drift linter
# on the merged tree.
#
# Catches the gap left by the commit-time readme-drift hook: a branch
# can be self-consistent at commit time and still fail CI's drift check
# if main has moved past it (e.g., another PR added an app and bumped
# the badges in the meantime). CI runs the linter against the merge
# tree; this hook does the same locally so the failure surfaces before
# push instead of after.
#
# Wired via .pre-commit-config.yaml stages: [pre-push].
set -euo pipefail

LINTER="tools/lint-readme-drift.py"
REMOTE="${REMOTE:-origin}"
BASE_BRANCH="${BASE_BRANCH:-main}"

if [ ! -x "$LINTER" ]; then
    echo "drift-check: $LINTER not executable; skipping" >&2
    exit 0
fi

# Fetch latest base. Degrade to no-op if offline — CI will still catch
# anything we miss, and a hard fail here would block legitimate pushes
# when the network is down.
if ! git fetch -q "$REMOTE" "$BASE_BRANCH" 2>/dev/null; then
    echo "drift-check: could not fetch $REMOTE/$BASE_BRANCH; skipping" >&2
    exit 0
fi

BASE_REF="refs/remotes/$REMOTE/$BASE_BRANCH"
HEAD_SHA=$(git rev-parse HEAD)
BASE_SHA=$(git rev-parse "$BASE_REF")

# If HEAD already contains BASE_SHA (no behind-ness), the local linter
# is sufficient — no merge to simulate.
if git merge-base --is-ancestor "$BASE_SHA" "$HEAD_SHA"; then
    exec "$LINTER"
fi

# Compute the merge tree. --write-tree refuses on conflicts (non-zero
# exit), which is itself a signal worth blocking on.
if ! MERGE_TREE=$(git merge-tree --write-tree "$HEAD_SHA" "$BASE_SHA" 2>/dev/null); then
    echo "drift-check: merging $REMOTE/$BASE_BRANCH would conflict — rebase or merge first" >&2
    exit 1
fi

# Materialize the merged tree as a detached worktree so we can run the
# linter against it without polluting the current working tree.
TMP_WT=$(mktemp -d)
trap 'git worktree remove --force "$TMP_WT" >/dev/null 2>&1 || true; rm -rf "$TMP_WT"' EXIT

MERGE_COMMIT=$(git commit-tree "$MERGE_TREE" -p "$HEAD_SHA" -p "$BASE_SHA" -m "drift-check tmp")
git worktree add --detach -q "$TMP_WT" "$MERGE_COMMIT"

(cd "$TMP_WT" && python3 "$LINTER")
