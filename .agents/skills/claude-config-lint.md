---
name: claude-config-lint
description: Audit Claude config health across all repos — verify @-import targets exist, check skill frontmatter completeness in every repo with .agents/skills/, verify MEMORY.md load budget across all non-worktree namespaces, and confirm shared persona base exists.
last_verified: 2026-07-03
---

# Claude config lint

One-shot audit of the Claude configuration surface. Run before making
changes to CLAUDE.md, operators, or instructions files to establish a
baseline, and again after to confirm nothing broke.

## When to use

- Before and after modifying any `CLAUDE.md`, `.agents/instructions/`,
  `.agents/skills/`, or `~/.claude-personal/` files.
- When a session seems to be missing context (instructions not firing,
  operator behavior unexpected).
- Weekly, as part of upstream-watcher hygiene.

## Checks

Run each check block in order. Failures are printed; silence = pass.

### 1. Symlink and shared-base integrity

```bash
# Global CLAUDE.md symlink chain
ls -la ~/.claude-personal/CLAUDE.md ~/.claude-personal/HOMELAB-SPEC.md ~/.claude-personal/memory

# Vault backing
test -f ~/vaults/claude/user/CLAUDE.md \
  && echo "vault CLAUDE.md OK" || echo "FAIL: vault CLAUDE.md missing"
test -f ~/vaults/claude/user/HOMELAB-SPEC.md \
  && echo "vault HOMELAB-SPEC.md OK" || echo "FAIL: vault HOMELAB-SPEC.md missing"

# Shared persona base (created by PF-05)
test -f ~/.claude-personal/rules/persona-base.md \
  && echo "persona-base.md OK" || echo "FAIL: ~/.claude-personal/rules/persona-base.md missing"
```

### 2. @-import targets — auto-discovered across all repos

```bash
# Discover all CLAUDE.md files: workspace repos + user-global, exclude worktrees.
# Use mapfile to avoid bash word-splitting on paths with spaces (e.g. "3532 Foxhall/CLAUDE.md").
mapfile -t CLAUDE_MDs < <(
  find ~/workspace/claude-workspace -maxdepth 2 -name 'CLAUDE.md' \
      ! -path '*worktrees*' 2>/dev/null
)
CLAUDE_MDs+=("$HOME/.claude-personal/CLAUDE.md")

for claude_md in "${CLAUDE_MDs[@]}"; do
  [ -f "$claude_md" ] || continue
  dir=$(dirname "$claude_md")
  real_dir=$(readlink -f "$dir")
  echo "=== $claude_md ==="
  grep -E '^@' "$claude_md" | while read -r import_line; do
    target="${import_line#@}"
    # expand leading ~
    target="${target/#\~/$HOME}"
    # relative → absolute
    [[ "$target" != /* ]] && target="$real_dir/$target"
    [ -f "$target" ] || echo "  MISSING: ${import_line#@}"
  done
done
```

### 3. Skill frontmatter completeness — all repos with .agents/skills/

```bash
# Repo skills
for skills_dir in ~/workspace/claude-workspace/*/.agents/skills; do
  [ -d "$skills_dir" ] || continue
  for f in "$skills_dir"/*.md; do
    [ -f "$f" ] || continue
    grep -q '^name:'        "$f" || echo "MISSING name:        in $f"
    grep -q '^description:' "$f" || echo "MISSING description: in $f"
  done
done

# User-global skills (PF-03 creates this dir; skip gracefully if absent)
[ -d ~/.claude-personal/skills ] && \
  find ~/.claude-personal/skills -name '*.md' | while read -r f; do
    grep -q '^name:'        "$f" || echo "MISSING name:        in $f"
    grep -q '^description:' "$f" || echo "MISSING description: in $f"
  done
```

### 4. Operator persona frontmatter

```bash
for f in ~/.claude-personal/agents/*.md; do
  [[ "$f" == */_shared/* ]] && continue
  for field in name model description tools; do
    grep -q "^$field:" "$f" || echo "MISSING $field: in $f"
  done
done
```

### 5. MEMORY.md load budget — all non-worktree project namespaces

```bash
echo "--- MEMORY.md line counts (budget: 200 lines each) ---"
find ~/.claude-personal/projects -name 'MEMORY.md' \
  | grep -v 'worktrees' \
  | sort \
  | while read -r mem; do
    lines=$(wc -l < "$mem")
    maxlen=$(awk 'length > max {max = length} END {print max}' "$mem")
    printf "%-80s %4d lines (max line: %d chars)\n" "$mem" "$lines" "$maxlen"
    [ "$lines" -gt 200 ] && echo "  WARN: exceeds 200-line load budget → archive overflow"
    [ "$maxlen" -gt 150 ] && echo "  WARN: line(s) > 150 chars → trim for scan-ability"
  done
```

### 6. Shared MCP reference in all operators

```bash
test -f ~/.claude-personal/agents/_shared/mcp-tool-loading.md \
  && echo "shared mcp-tool-loading.md OK" \
  || echo "FAIL: ~/.claude-personal/agents/_shared/mcp-tool-loading.md missing"

for f in ~/.claude-personal/agents/*.md; do
  [[ "$f" == */_shared/* ]] && continue
  grep -q "mcp-tool-loading.md\|reference_lovenet_gateway_mcp_tool_prefixes" "$f" \
    || echo "WARN: $f has no MCP loading reference"
done
```

### 7. Per-repo persona.md references shared base (post-PF-05)

```bash
echo "--- persona.md shared-base pointer check ---"
for persona in ~/workspace/claude-workspace/*/.agents/instructions/persona.md; do
  [ -f "$persona" ] || continue
  grep -q 'persona-base.md' "$persona" \
    || echo "WARN: no shared-base reference in $persona"
done
```

## Interpreting results

| Output | Meaning | Fix |
|---|---|---|
| `FAIL: vault CLAUDE.md missing` | Symlink broken | Restore vault file or fix symlink |
| `FAIL: persona-base.md missing` | PF-05 not executed | Run PF-05 Change 1 |
| `MISSING: <path>` | `@`-import target doesn't exist | Restore the file or remove the import |
| `MISSING name: in <file>` | Skill frontmatter incomplete | Add the missing frontmatter field |
| `WARN: exceeds 200-line load budget` | MEMORY.md too long; older entries invisible | Archive overflow to `MEMORY-archive-*.md`; run PF-04 for home-ops |
| `WARN: line(s) > 150 chars` | Entries too verbose for clean scanning | Trim to one-line hooks |
| `WARN: no MCP loading reference` | Operator still has inline MCP prose | Replace with pointer to `_shared/mcp-tool-loading.md` |
| `WARN: no shared-base reference` | Persona not yet slimmed down (PF-05 pending) | Apply persona slim-down from PF-05 |

## What this is NOT

- Not a runtime test — it doesn't invoke any tool or verify MCP connectivity.
  It only checks the config files on disk.
- Not a substitute for reading the files — it catches structural gaps,
  not semantic ones (e.g. stale content in an operator).
