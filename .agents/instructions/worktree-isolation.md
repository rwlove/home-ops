# Per-session git worktree isolation

Multiple Claude (or other agent) sessions run concurrently in this
repo. They MUST NOT share the primary working checkout.

## Rule

- **Never do mutating git work in the primary checkout**
  (`~/workspace/claude-workspace/home-ops` itself). Branch creation,
  staging, commits, saved-aside working snapshots, rebases, and
  branch switches there are shared across every concurrent session —
  they collide.
- **Each session gets its own dated worktree** under
  `home-ops.worktrees/<YYYY-MM-DD-hhhh>`, on its own `claude/<...>`
  branch, created off `origin/main`:

  ```bash
  git worktree add ../home-ops.worktrees/$(date +%Y-%m-%d)-<slug> \
    -b <branch> origin/main
  ```

  Do all edits, commits, and pushes from inside that worktree
  (`git -C <worktree> ...` or `cd` into it). The object store is
  shared; the index, HEAD, and working tree are not.
- **Clean up when merged:** `git worktree remove <path>` then
  `git branch -D <branch>`.

## Why

The primary checkout has a single index, HEAD, and working tree. Two
sessions operating there at once will clobber each other: saving
working changes aside or a branch switch from one session reverts the
other's uncommitted edits and moves HEAD out from under an in-flight
`git switch -c`. This looks like data loss but is really a shared-state
collision. The fleet of dated worktrees already in `git worktree
list` is the established pattern; this file makes it a rule rather
than a convention enforced only by tooling.

## Recovery if you got collided

1. `git diff > ~/<slug>.patch` — make the work durable outside the
   repo immediately.
2. `git worktree add ../home-ops.worktrees/<dated> -b <branch> origin/main`.
3. `git -C <worktree> apply ~/<slug>.patch`, then stage/commit/push
   from the worktree.

## What this is NOT

- Not a rule about subagents — the Agent tool's `isolation:
  "worktree"` already isolates those. This is about top-level
  interactive sessions sharing the primary checkout.
- Not a fix for the separate `.git/objects` SELinux relabel race
  (`container_file_t` → intermittent `insufficient permission` /
  `unable to read tree`). That is a container mounting `.git`
  writable; clear it with `sudo restorecon -Rv .git`.
