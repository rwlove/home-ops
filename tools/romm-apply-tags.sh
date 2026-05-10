#!/usr/bin/env bash
# Apply a "tag" (Romm Collection) to every ROM listed in a manifest.
#
# Romm 4.x has no tag CRUD API — the `tags` field on a ROM is read-only
# and populated from filename parsing. The closest editable equivalent
# is a Collection: a named, user-curated group of rom_ids. This script
# creates (or finds) a Collection named after the tag and PUTs the
# matched rom_ids onto it.
#
# Usage:
#   tools/romm-apply-tags.sh --manifest <path> --tag <name> [--dry-run]
#
# Manifest format:
#   - Header lines start with `#`; recognised directives:
#       # tag: <name>            (default for --tag if not given on CLI)
#       # platform: <fs_slug>    (default platform for bare-filename lines)
#   - Data lines:
#       "<platform_fs_slug>/<filename>" — multi-platform manifest
#       "<filename>"                    — single-platform; needs # platform:
#
# Auth (any of, in order):
#   1. ROMM_USERNAME + ROMM_API_KEY env vars
#   2. 1Password: op read 'op://kubernetes/romm/username|API_KEY'
#
# Other env:
#   ROMM_BASE_URL — default: https://romm.${SECRET_DOMAIN:-thesteamedcrab.com}

set -euo pipefail

usage() {
  sed -n '/^# /,/^$/p' "$0" | sed -E 's/^# ?//' >&2
  exit 1
}

MANIFEST=""
TAG=""
DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest) MANIFEST="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage ;;
    *) echo "unknown arg: $1" >&2; usage ;;
  esac
done
[[ -n "$MANIFEST" ]] || usage
[[ -f "$MANIFEST" ]] || { echo "manifest not found: $MANIFEST" >&2; exit 1; }

# pull defaults from manifest header if not provided on CLI
DEFAULT_PLATFORM=$(grep -E '^# platform: ' "$MANIFEST" | head -1 | sed 's/^# platform: //')
[[ -z "$TAG" ]] && TAG=$(grep -E '^# tag: ' "$MANIFEST" | head -1 | sed 's/^# tag: //')
[[ -n "$TAG" ]] || { echo "no --tag given and no '# tag:' header in manifest" >&2; exit 1; }

: "${ROMM_BASE_URL:=https://romm.${SECRET_DOMAIN:-thesteamedcrab.com}}"
if [[ -z "${ROMM_USERNAME:-}" ]]; then
  ROMM_USERNAME=$(op read 'op://kubernetes/romm/username' 2>/dev/null || true)
fi
if [[ -z "${ROMM_API_KEY:-}" ]]; then
  ROMM_API_KEY=$(op read 'op://kubernetes/romm/API_KEY' 2>/dev/null || true)
fi
[[ -n "$ROMM_USERNAME" && -n "$ROMM_API_KEY" ]] || {
  echo "auth missing — set ROMM_USERNAME + ROMM_API_KEY (or store in 1P 'romm' item)" >&2
  exit 1
}

api() {
  curl -fsS -u "$ROMM_USERNAME:$ROMM_API_KEY" "$@"
}

echo ">>> tag '$TAG'  manifest '$MANIFEST'  base $ROMM_BASE_URL"

# 1) platform fs_slug -> id map
declare -A PLATFORM_IDS
while IFS=$'\t' read -r slug id; do
  PLATFORM_IDS["$slug"]="$id"
done < <(api "$ROMM_BASE_URL/api/platforms" | jq -r '.[] | "\(.fs_slug)\t\(.id)"')
echo "    indexed ${#PLATFORM_IDS[@]} platforms"

# 2) parse manifest, group by platform
declare -A PLATFORM_FILES
while IFS= read -r line; do
  [[ -z "$line" || "$line" == \#* ]] && continue
  if [[ "$line" == */* ]]; then
    plat="${line%%/*}"; file="${line#*/}"
  else
    plat="$DEFAULT_PLATFORM"; file="$line"
  fi
  [[ -n "$plat" ]] || { echo "  no platform for line: $line" >&2; continue; }
  PLATFORM_FILES["$plat"]+="$file"$'\n'
done < "$MANIFEST"

# 3) per-platform: paginate /api/roms, build fs_name -> id map, match
declare -a MATCHED_IDS=()
declare -i total=0 matched=0 missing=0
for plat in "${!PLATFORM_FILES[@]}"; do
  pid="${PLATFORM_IDS[$plat]:-}"
  if [[ -z "$pid" ]]; then
    echo "  unknown platform '$plat' — skipping" >&2
    while IFS= read -r f; do [[ -n "$f" ]] && missing+=1 && total+=1; done <<<"${PLATFORM_FILES[$plat]}"
    continue
  fi

  declare -A FILES_TO_ID=()
  offset=0; limit=500
  while :; do
    page=$(api "$ROMM_BASE_URL/api/roms?platform_ids=$pid&limit=$limit&offset=$offset&with_char_index=false&with_filter_values=false")
    pagecount=$(jq '.items | length' <<<"$page")
    [[ "$pagecount" == 0 ]] && break
    while IFS=$'\t' read -r fn id; do
      FILES_TO_ID["$fn"]="$id"
    done < <(jq -r '.items[] | "\(.fs_name)\t\(.id)"' <<<"$page")
    (( pagecount < limit )) && break
    offset=$(( offset + limit ))
  done
  echo "  [$plat] indexed ${#FILES_TO_ID[@]} ROMs"

  while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    total+=1
    rid="${FILES_TO_ID[$file]:-}"
    if [[ -n "$rid" ]]; then
      MATCHED_IDS+=("$rid"); matched+=1
    else
      missing+=1
      echo "    miss: $plat/$file" >&2
    fi
  done <<<"${PLATFORM_FILES[$plat]}"

  unset FILES_TO_ID
done

echo ""
echo "=== summary ==="
echo "manifest entries: $total"
echo "matched ROMs:     $matched"
echo "missing:          $missing"

if [[ "$matched" -eq 0 ]]; then
  echo "no matches; nothing to update"
  exit 0
fi

if [[ "$DRY_RUN" == 1 ]]; then
  echo "dry-run: would set collection '$TAG' rom_ids to ${matched} entries"
  exit 0
fi

# 4) find or create the collection
echo ">>> ensuring collection '$TAG'"
existing=$(api "$ROMM_BASE_URL/api/collections" | jq -r --arg n "$TAG" '.[] | select(.name==$n) | .id' | head -1)
if [[ -n "$existing" ]]; then
  cid="$existing"
  echo "    found id=$cid"
else
  cid=$(api -X POST "$ROMM_BASE_URL/api/collections" \
    -F "name=$TAG" \
    -F "description=Auto-tagged via $(basename "$MANIFEST")" \
    | jq -r '.id')
  echo "    created id=$cid"
fi

# 5) PUT rom_ids (JSON array string per OpenAPI spec)
ids_json=$(printf '%s\n' "${MATCHED_IDS[@]}" | jq -Rs 'split("\n") | map(select(. != "")) | map(tonumber)')
echo ">>> PUT /api/collections/$cid  rom_ids=[$matched values]"
api -X PUT "$ROMM_BASE_URL/api/collections/$cid" \
  -F "rom_ids=$ids_json" \
  >/dev/null
echo "    done"
