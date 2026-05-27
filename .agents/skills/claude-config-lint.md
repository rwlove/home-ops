---
name: claude-config-lint
description: Audit Claude config health — verify @-import targets exist, symlinks are intact, skill frontmatter is complete, and MEMORY.md is within its load budget.
last_verified: 2026-05-25
---

# Claude config lint

One-shot audit of the Claude configuration surface. Run before making
changes to CLAUDE.md, operators, or instructions files to establish a
baseline, and again after to confirm nothing broke.

## When to use

- Before and after modifying any `.claude.md`, `.agents/instructions/`,
  `.agents/skills/`, or `~/.claude-personal/agents/` files.
- When a session seems to be missing context (instructions not firing,
  operator behavior unexpected).
- Weekly, as part of upstream-watcher hygiene.

## Checks

Run each check block in order. Failures are printed; silence = pass.

### 1. Symlink integrity

```bash
# Global CLAUDE.md symlinks
ls -la ~/.claude-personal/CLAUDE.md ~/.claude-personal/HOMELAB-SPEC.md ~/.claude-personal/memory

# Vault backing exists
test -f ~/vaults/claude/user/CLAUDE.md && echo "vault CLAUDE.md OK" || echo "FAIL: vault CLAUDE.md missing"
test -f ~/vaults/claude/user/HOMELAB-SPEC.md && echo "vault HOMELAB-SPEC.md OK" || echo "FAIL: vault HOMELAB-SPEC.md missing"
```

### 2. `@`-import targets in CLAUDE.md files

```bash
# For each CLAUDE.md in the workspace, verify every @-import resolves
for claude_md in \
    ~/.claude-personal/CLAUDE.md \
    ~/workspace/claude-workspace/home-ops/CLAUDE.md \
    ~/workspace/claude-workspace/home-assistant-config/CLAUDE.md \
    ~/workspace/claude-workspace/langgraph-agents/CLAUDE.md; do
  [ -f "$claude_md" ] || continue
  dir=$(dirname "$claude_md")
  # Resolve symlinks for the dir
  real_dir=$(readlink -f "$dir")
  echo "=== $claude_md ==="
  grep -E '^@' "$claude_md" | while read -r import_line; do
    target="${import_line#@}"
    # Expand relative path
    full="$real_dir/$target"
    [ -f "$full" ] || echo "  MISSING: $target"
  done
done
```

### 3. Skill frontmatter completeness

```bash
# Every skill in home-ops .agents/skills/ must have name: and description:
for f in ~/workspace/claude-workspace/home-ops/.agents/skills/*.md; do
  name=$(grep -m1 '^name:' "$f")
  desc=$(grep -m1 '^description:' "$f")
  [ -z "$name" ] && echo "MISSING name: in $f"
  [ -z "$desc" ] && echo "MISSING description: in $f"
done
```

### 4. Operator persona frontmatter

```bash
# Every operator must have name:, model:, description:, tools:
for f in ~/.claude-personal/agents/*.md; do
  [[ "$f" == */_shared/* ]] && continue
  for field in name model description tools; do
    grep -q "^$field:" "$f" || echo "MISSING $field: in $f"
  done
done
```

### 5. MEMORY.md load budget

```bash
MEM=~/.claude-personal/projects/-home-rwlove-workspace-claude-workspace-home-ops/memory/MEMORY.md
lines=$(wc -l < "$MEM")
maxlen=$(awk 'length > max {max=length} END {print max}' "$MEM")
long=$(awk 'length > 150' "$MEM" | wc -l)
echo "MEMORY.md: $lines lines (budget: 200) | max line: $maxlen chars | over-150: $long"
[ "$lines" -gt 200 ] && echo "WARN: MEMORY.md exceeds 200-line load budget"
[ "$long" -gt 10 ] && echo "WARN: $long entries exceed 150-char line length"
```

### 6. Shared MCP reference exists

```bash
test -f ~/.claude-personal/agents/_shared/mcp-tool-loading.md \
  && echo "shared mcp-tool-loading.md OK" \
  || echo "FAIL: ~/.claude-personal/agents/_shared/mcp-tool-loading.md missing"

# Verify all operators reference it (not the old inline pattern)
for f in ~/.claude-personal/agents/*.md; do
  [[ "$f" == */_shared/* ]] && continue
  grep -q "mcp-tool-loading.md\|reference_lovenet_gateway_mcp_tool_prefixes" "$f" \
    || echo "WARN: $f has no MCP loading reference"
done
```

## Interpreting results

| Output | Meaning | Fix |
|---|---|---|
| `MISSING: <path>` | `@`-import target doesn't exist | Restore the file or remove the import |
| `MISSING name: in <file>` | Frontmatter incomplete | Add the missing frontmatter field |
| `WARN: MEMORY.md exceeds 200-line` | Index too long; older entries invisible | Archive overflow to `MEMORY-archive-*.md` |
| `WARN: N entries exceed 150-char` | Entries too verbose for clean scanning | Trim descriptions to one-line hooks |
| `WARN: <file> has no MCP loading reference` | Operator still has inline MCP prose | Replace with pointer to `_shared/mcp-tool-loading.md` |

## What this is NOT

- Not a runtime test — it doesn't invoke any tool or verify MCP
  connectivity. It only checks the config files on disk.
- Not a substitute for reading the files — it catches structural gaps,
  not semantic ones (e.g. stale content in an operator).
