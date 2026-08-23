#!/usr/bin/env bash
#
# immich-frame-video-transcode.sh
#
# Give the ImmichFrame album's videos a browser-playable (h264) stored transcode
# so they play on the Frameo frames (WebView 101 = h264 only), WITHOUT leaving
# Immich's global transcode policy enabled.
#
# Rob's rule: ONLY the frame album's videos ever get transcoded — never the wider
# library, not even future uploads elsewhere. So the global policy stays
# `disabled`; this tool briefly flips it to `required`, transcodes ONLY the
# album's non-h264 videos, then reverts to `disabled`. The produced transcodes
# persist after the revert (Immich only deletes a transcode when a job re-runs on
# the asset under a non-transcoding policy).
#
# Why the flip is unavoidable: Immich transcode is global-policy-driven. With
# `ffmpeg.transcode=disabled`, a targeted transcode-video job NO-OPS (verified
# live, Immich v3.1.0). There is no per-asset policy override and no live config
# API (system.json is file-managed). See memory:
#   project_todo_immichframe_video_transcode
#
# GitOps-clean: the policy change goes through git (a PR against the immich
# ExternalSecret), never a live `kubectl patch` of the Flux/ESO-managed secret.
# A restart applies the subPath-mounted config. An EXIT trap force-reverts to
# `disabled` if anything dies mid-run, so the library is never left with
# auto-transcode silently on.
#
# Usage:
#   tools/immich-frame-video-transcode.sh [-y] [ALBUM_ID ...]
#     -y            skip the confirmation prompt (restarts immich-server twice)
#     ALBUM_ID ...  one or more Immich album UUIDs (default: familyroom frame album)
#
# Requires on the invoking host: git, gh, flux, kubectl, op, jq, and a
# cluster-admin kubeconfig. The Immich API key is read from 1Password item
# `immich`, field `immichframe_apikey`.

set -euo pipefail

# ---- config ----
NS=media
STS=immich-server
POD=immich-server-0
CTR=main
ES_PATH="kubernetes/apps/media/immich/app/externalsecret.yaml"
KUSTOMIZATION=cluster-apps
ORG_REPO=rwlove/home-ops
MAIN=main
DEFAULT_ALBUM="ad782b0e-9e90-453c-98e7-086455300ef1"   # familyroom frame album
# party frame album (currently has 0 videos): 59526fba-9726-4976-a6da-b7f71ebd16cd

# ---- args ----
ASSUME_YES=0
ALBUMS=()
for a in "$@"; do
  case "$a" in
    -y|--yes) ASSUME_YES=1 ;;
    -*) echo "unknown flag: $a" >&2; exit 2 ;;
    *)  ALBUMS+=("$a") ;;
  esac
done
[ ${#ALBUMS[@]} -eq 0 ] && ALBUMS=("$DEFAULT_ALBUM")

REPO="$(git rev-parse --show-toplevel)"

c()   { printf '\033[1;36m[%s] %s\033[0m\n' "$(date +%H:%M:%S)" "$*"; }
warn(){ printf '\033[1;33m[WARN] %s\033[0m\n' "$*" >&2; }
die() { printf '\033[1;31m[FATAL] %s\033[0m\n' "$*" >&2; exit 1; }

for bin in git gh flux kubectl op jq; do command -v "$bin" >/dev/null || die "missing dependency: $bin"; done

KEY="$(op item get immich --fields immichframe_apikey --reveal 2>/dev/null)" || die "cannot read immich API key from 1Password"
[ -n "$KEY" ] || die "empty immich API key"

# ---- helpers ----
api() { # api METHOD PATH [JSON_BODY]
  local m="$1" p="$2" body="${3:-}"
  if [ -n "$body" ]; then
    kubectl exec -n "$NS" "$POD" -c "$CTR" -- sh -c \
      "curl -s -X $m -H 'x-api-key: $KEY' -H 'content-type: application/json' -d '$body' http://localhost:2283$p"
  else
    kubectl exec -n "$NS" "$POD" -c "$CTR" -- sh -c \
      "curl -s -H 'x-api-key: $KEY' http://localhost:2283$p"
  fi
}
policy_live() { api GET /api/system-config | jq -r '.ffmpeg.transcode'; }
ffprobe_pod() { # ffprobe_pod <path>  -> video codec_name (empty if unreadable)
  kubectl exec -n "$NS" "$POD" -c "$CTR" -- sh -c \
    'FF=$(command -v ffprobe || echo /usr/lib/jellyfin-ffmpeg/ffprobe); "$FF" -v error -select_streams v:0 -show_entries stream=codec_name -of csv=p=0 "'"$1"'" 2>/dev/null'
}
encoded_ids() { # asset-ids that currently have an encoded-video file
  kubectl exec -n "$NS" "$POD" -c "$CTR" -- sh -c \
    'find /data/encoded-video -type f -name "*.mp4" 2>/dev/null' \
    | sed -E 's|.*/([0-9a-f-]{36})[^/]*\.mp4$|\1|' | sort -u
}

set_policy_via_pr() { # set_policy_via_pr <disabled|required> <subject>
  local target="$1" subject="$2" wt br pr
  wt="$(mktemp -d)"; br="claude/immich-transcode-${target}-$$"
  git -C "$REPO" worktree add -q "$wt" -b "$br" "origin/$MAIN"
  sed -i -E 's/("transcode": )"(disabled|required)"/\1"'"$target"'"/' "$wt/$ES_PATH"
  git -C "$wt" add "$ES_PATH"
  git -C "$wt" commit -q -m "$subject" \
    -m "Automated by tools/immich-frame-video-transcode.sh (temporary flip-scope-revert)." \
    -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
  git -C "$wt" push -q -u origin "$br"
  pr="$(gh pr create -R "$ORG_REPO" --head "$br" --title "$subject" \
        --body "Automated flip-scope-revert step — see tools/immich-frame-video-transcode.sh." | tail -1)"
  c "PR $pr — waiting for CI"
  gh pr checks "$pr" -R "$ORG_REPO" --watch --interval 20 >/dev/null || { git -C "$REPO" worktree remove --force "$wt"; die "CI failed on $pr"; }
  gh pr merge "$pr" -R "$ORG_REPO" --squash >/dev/null
  git -C "$REPO" worktree remove --force "$wt" 2>/dev/null || true
  git push origin --delete "$br" >/dev/null 2>&1 || true
  # apply: reconcile git -> re-render secret -> restart to load subPath config
  flux reconcile kustomization "$KUSTOMIZATION" >/dev/null 2>&1 || true
  kubectl annotate externalsecret immich -n "$NS" "force-sync=$(date +%s)" --overwrite >/dev/null
  sleep 6
  kubectl rollout restart "statefulset/$STS" -n "$NS" >/dev/null
  kubectl rollout status "statefulset/$STS" -n "$NS" --timeout=180s >/dev/null
  [ "$(policy_live)" = "$target" ] || die "live policy did not become '$target' after restart"
  c "global ffmpeg.transcode is now: $target"
}

FLIPPED=0
cleanup() {
  local rc=$?
  if [ "$FLIPPED" = 1 ]; then
    local p; p="$(policy_live 2>/dev/null || echo unknown)"
    if [ "$p" != "disabled" ]; then
      warn "run ended with policy='$p' — force-reverting to disabled"
      FLIPPED=0
      set_policy_via_pr disabled "chore(immich): emergency revert transcode->disabled" \
        || warn "EMERGENCY REVERT FAILED — set \"transcode\":\"disabled\" in $ES_PATH and restart $STS MANUALLY."
    fi
  fi
  exit $rc
}
trap cleanup EXIT

# ---- 1. inventory album videos + codecs (no downloads; in-pod ffprobe) ----
c "current global policy: $(policy_live)"
declare -a NEED=()          # asset ids that are not h264 -> need transcode
for alb in "${ALBUMS[@]}"; do
  c "scanning album $alb"
  # NOTE: single search page (<=250 assets). Frame albums are small.
  while IFS=$'\t' read -r id path; do
    [ -n "$id" ] || continue
    codec="$(ffprobe_pod "$path")"
    if [ "$codec" = "h264" ]; then
      c "  ok      $id  ($codec)"
    else
      c "  needs   $id  ($codec)"
      NEED+=("$id")
    fi
  done < <(api POST /api/search/metadata "{\"albumIds\":[\"$alb\"],\"type\":\"VIDEO\"}" \
             | jq -r '.assets.items[]? | .id + "\t" + .originalPath')
done

if [ ${#NEED[@]} -eq 0 ]; then
  c "all album videos are already h264 — nothing to transcode. Done."
  exit 0
fi
c "${#NEED[@]} video(s) need a transcode: ${NEED[*]}"

if [ "$ASSUME_YES" != 1 ]; then
  printf '\nThis will temporarily flip Immich transcode policy to required and RESTART %s TWICE (Renee-facing photo/frame blip). Proceed? [y/N] ' "$STS"
  read -r ans; [ "$ans" = y ] || [ "$ans" = Y ] || die "aborted by user"
fi

# ---- 2. baseline, flip, transcode, leak-check ----
mapfile -t BASELINE < <(encoded_ids)
c "baseline: ${#BASELINE[@]} assets already have an encoded video"

set_policy_via_pr required "chore(immich): temporarily enable transcode for frame-album video"
FLIPPED=1

ids_json="$(printf '%s\n' "${NEED[@]}" | jq -R . | jq -sc .)"
c "submitting transcode-video jobs for ${#NEED[@]} asset(s)"
api POST /api/assets/jobs "{\"assetIds\":$ids_json,\"name\":\"transcode-video\"}" >/dev/null

c "waiting for videoConversion queue to drain"
for _ in $(seq 1 150); do
  counts="$(api GET /api/jobs | jq -c '.videoConversion.jobCounts')"
  a="$(jq -r '.active' <<<"$counts")"; w="$(jq -r '.waiting' <<<"$counts")"
  [ "${a:-0}" = 0 ] && [ "${w:-0}" = 0 ] && break
  sleep 4
done

# leak-check: any NEW encoded asset must be one we targeted
mapfile -t AFTER < <(encoded_ids)
new="$(comm -13 <(printf '%s\n' "${BASELINE[@]}" | sort -u) <(printf '%s\n' "${AFTER[@]}" | sort -u))"
stray=""
while read -r id; do
  [ -n "$id" ] || continue
  printf '%s\n' "${NEED[@]}" | grep -qx "$id" || stray+="$id "
done <<<"$new"
[ -n "$stray" ] && warn "UNEXPECTED transcodes for non-targeted asset(s): $stray (a new upload during the window?) — review/delete."

# ---- 3. revert ----
set_policy_via_pr disabled "chore(immich): revert transcode policy to disabled (frame-album video done)"
FLIPPED=0

# ---- 4. verify each targeted asset now serves h264 ----
c "verifying targeted assets serve h264"
ok=1
for id in "${NEED[@]}"; do
  enc="$(kubectl exec -n "$NS" "$POD" -c "$CTR" -- sh -c 'find /data/encoded-video -type f -name "'"$id"'*.mp4" 2>/dev/null | head -1')"
  if [ -z "$enc" ]; then warn "  $id: NO encoded file produced"; ok=0; continue; fi
  codec="$(ffprobe_pod "$enc")"
  if [ "$codec" = "h264" ]; then c "  $id: encoded h264 ✓"; else warn "  $id: encoded codec is '$codec' (expected h264)"; ok=0; fi
done

c "final global policy: $(policy_live)"
[ "$ok" = 1 ] && c "DONE — frame-album videos are h264-playable; global policy back to disabled." \
             || die "one or more targeted assets did not end up h264 — investigate."
