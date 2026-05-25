#!/usr/bin/env bash
# Check that container images use ghcr.io, or are explicitly allowlisted.
#
# Usage:
#   tools/check-image-registry.sh < images.txt           # one image per line
#   echo '["a:1","b:2"]' | tools/check-image-registry.sh --json
#   tools/check-image-registry.sh --json images.json
#
# Reads from stdin or the file given as the last positional arg.
# Pass --json if the input is a JSON array of image strings (e.g. the
# output of `flux-local get cluster --only-images --output json`).
#
# Exits 0 if all images are ghcr.io or allowlisted; 1 otherwise.
# Prints offenders to stderr with the allowlist path so the fix is
# obvious.

set -euo pipefail

ALLOWLIST="${ALLOWLIST:-$(dirname "$0")/../.github/image-registry-allowlist.txt}"

json_mode=false
input_file=""
for arg in "$@"; do
  case "$arg" in
    --json) json_mode=true ;;
    -h|--help)
      sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) input_file="$arg" ;;
  esac
done

if [[ ! -f "$ALLOWLIST" ]]; then
  echo "error: allowlist not found at $ALLOWLIST" >&2
  exit 2
fi

# Build prefix list (strip comments and blank lines)
mapfile -t prefixes < <(sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$ALLOWLIST")

# Read images
if [[ -n "$input_file" ]]; then
  raw="$(cat "$input_file")"
else
  raw="$(cat)"
fi

if $json_mode; then
  mapfile -t images < <(printf '%s' "$raw" | jq -r '.[]')
else
  mapfile -t images < <(printf '%s\n' "$raw" | sed -e '/^[[:space:]]*$/d')
fi

violations=()
for image in "${images[@]}"; do
  # Strip @sha256:... and :tag for the prefix match (but keep the full
  # ref in the error message).
  bare="${image%@*}"
  bare="${bare%:*}"

  # ghcr.io — always OK
  if [[ "$bare" == ghcr.io/* ]]; then
    continue
  fi

  # Normalize bare docker.io paths to their fully-qualified
  # `docker.io/<namespace>/<image>` form. Docker Hub treats unspecified
  # namespace as `library/`, so all four of these are equivalent:
  #   busybox / library/busybox / docker.io/busybox / docker.io/library/busybox
  # The allowlist uses the `docker.io/library/...` form for clarity.
  case "$bare" in
    docker.io/*/* ) ;;                            # `docker.io/org/img` — canonical
    docker.io/* )
      bare="docker.io/library/${bare#docker.io/}" ;;  # `docker.io/img` → `docker.io/library/img`
    *.*/* ) ;;                                    # other registry host
    */* )   bare="docker.io/$bare" ;;             # `org/img`
    * )     bare="docker.io/library/$bare" ;;     # `img`
  esac

  matched=false
  for p in "${prefixes[@]}"; do
    if [[ "$bare" == "$p"* ]]; then
      matched=true
      break
    fi
  done

  $matched || violations+=("$image")
done

if (( ${#violations[@]} == 0 )); then
  echo "OK: all $(printf '%s\n' "${images[@]}" | wc -l) images use ghcr.io or are allowlisted."
  exit 0
fi

{
  echo "Non-ghcr.io images found that are not allowlisted:"
  printf '  - %s\n' "${violations[@]}"
  echo
  echo "Repo policy is to prefer ghcr.io. For each offender, either:"
  echo "  1. Switch to the upstream ghcr.io publication if one exists, or"
  echo "  2. Add the image's prefix to ${ALLOWLIST#"$PWD/"} with a one-line"
  echo "     reason (e.g. 'no ghcr publication', 'official canonical home')."
  echo
  echo "Check for a ghcr.io equivalent with:"
  echo "  skopeo list-tags docker://ghcr.io/<org>/<image>"
  echo "  skopeo list-tags docker://ghcr.io/home-operations/<image>"
} >&2

exit 1
