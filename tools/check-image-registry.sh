#!/usr/bin/env bash
# Check that container images do not pull from Docker Hub unless the
# image is explicitly allowlisted.
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
# Policy: Docker Hub (docker.io) is the only restricted registry — it
# rate-limits anonymous/free pulls. Every other registry (ghcr.io,
# quay.io, registry.k8s.io, nvcr.io, ...) is allowed. The allowlist
# holds docker.io images that have no acceptable alternative.
#
# Exits 0 if no non-allowlisted docker.io images are present; 1 otherwise.
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

  # Normalize bare/implicit paths to their fully-qualified
  # `docker.io/<namespace>/<image>` form. Docker Hub treats unspecified
  # namespace as `library/`, so all four of these are equivalent:
  #   busybox / library/busybox / docker.io/busybox / docker.io/library/busybox
  # This resolves short Docker Hub refs so they're caught by the gate
  # below. The allowlist uses the `docker.io/library/...` form for clarity.
  case "$bare" in
    docker.io/*/* ) ;;                            # `docker.io/org/img` — canonical
    docker.io/* )
      bare="docker.io/library/${bare#docker.io/}" ;;  # `docker.io/img` → `docker.io/library/img`
    *.*/* ) ;;                                    # other registry host
    */* )   bare="docker.io/$bare" ;;             # `org/img`
    * )     bare="docker.io/library/$bare" ;;     # `img`
  esac

  # Only Docker Hub is restricted (it rate-limits free pulls). Every
  # other registry — ghcr.io, quay.io, registry.k8s.io, nvcr.io, ... —
  # passes without an allowlist entry.
  if [[ "$bare" != docker.io/* ]]; then
    continue
  fi

  # docker.io image — must be explicitly allowlisted.
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
  echo "OK: none of the $(printf '%s\n' "${images[@]}" | wc -l) images pull from Docker Hub (or they are allowlisted)."
  exit 0
fi

{
  echo "Docker Hub (docker.io) images found that are not allowlisted:"
  printf '  - %s\n' "${violations[@]}"
  echo
  echo "Docker Hub rate-limits anonymous/free pulls, so it is restricted."
  echo "For each offender, either:"
  echo "  1. Switch to a non-docker.io publication (ghcr.io, quay.io,"
  echo "     registry.k8s.io, ... — any of these is fine), or"
  echo "  2. Add the image's prefix to ${ALLOWLIST#"$PWD/"} with a one-line"
  echo "     reason (e.g. 'no non-docker publication')."
  echo
  echo "Check for a ghcr.io equivalent with:"
  echo "  skopeo list-tags docker://ghcr.io/<org>/<image>"
  echo "  skopeo list-tags docker://ghcr.io/home-operations/<image>"
} >&2

exit 1
