#!/usr/bin/env bash
#
# immich-frame-video-transcode.sh
#
# Give the ImmichFrame album's videos a FRAME-PLAYABLE stored transcode — h264,
# short-side <=720, <=5 Mbps — so they play smoothly on the low-power Frameo frames
# (Allwinner sun50iw10, WebView 101 = h264 only; 4K h264 and 1080p60 stutter),
# WITHOUT leaving Immich's global transcode policy enabled.
#
# Rob's rule: ONLY the frame album's videos ever get transcoded — never the wider
# library, not even future uploads elsewhere. So the global policy stays
# `disabled`; this tool briefly flips it to `bitrate`, transcodes ONLY the album's
# non-conforming videos (wrong codec OR >1080p OR over the bitrate cap), then
# reverts to `disabled`. The produced transcodes persist after the revert (Immich
# only deletes a transcode when a job re-runs on the asset under a non-transcoding
# policy).
#
# Codec alone is not enough: the old `required` flip skipped 4K *h264* originals
# (already an accepted codec) and served them raw at ~72 Mbps — the frame can't
# decode 4K h264, so it stuttered. The `bitrate` policy + ffmpeg.maxBitrate +
# ffmpeg.targetResolution (both set in the immich ExternalSecret, inert while
# transcode=disabled) force those down to 720p/5 Mbps.
#
# Why the flip is unavoidable: Immich transcode is global-policy-driven. With
# `ffmpeg.transcode=disabled`, a targeted transcode-video job NO-OPS (verified
# live, Immich v3.1.0). There is no per-asset policy override and no live config
# API (system.json is file-managed). The resolution/bitrate caps live permanently
# in the ExternalSecret (ffmpeg.targetResolution + ffmpeg.maxBitrate) and do
# nothing until this tool flips the policy off `disabled`. See memory:
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

# A served video is "frame-conforming" only if h264 AND its SHORT side (min of
# width,height) is <= MAX_SHORT AND its overall bitrate is <= MAX_BITRATE_BPS.
# The ceilings sit a hair above the ffmpeg.targetResolution / ffmpeg.maxBitrate
# caps so a freshly-produced transcode isn't re-flagged.
#
# 720 (not 1080): the Allwinner sun50iw10 WebView <video> decoder stutters on
# 1080p60 h264 (~124M px/s) even at 8 Mbps on a healthy 5GHz link — verified the
# link is not the bottleneck. 720p halves pixels/sec and clears both the 60fps
# and 30fps clips. Framerate itself is unfixable via Immich (no fps knob), so
# resolution is the only decode-load lever the transcode pipeline exposes.
MAX_SHORT=720            # short side; 1280x720 or 720x1280 both pass, 1080p stutters
MAX_BITRATE_BPS=5500000  # ~5.5 Mbps; a hair over the 5 Mbps ffmpeg.maxBitrate cap

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

# Prefer a pre-fetched key from the environment (works when op's app-integration
# unlock can't be reached from a detached/background shell); fall back to op.
KEY="${IMMICH_API_KEY:-$(op item get immich --fields immichframe_apikey --reveal 2>/dev/null)}" \
  || die "cannot read immich API key (set IMMICH_API_KEY or sign in to 1Password)"
[ -n "$KEY" ] || die "empty immich API key (set IMMICH_API_KEY or sign in to 1Password)"

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
enc_path() { # enc_path <id> -> this asset's encoded-video file path (empty if none)
  kubectl exec -n "$NS" "$POD" -c "$CTR" -- sh -c \
    'find /data/encoded-video -type f -name "'"$1"'*.mp4" 2>/dev/null | head -1'
}
probe_served() { # probe_served <id> <originalPath> -> "codec shortside bitrate" of what the frame gets
  # The frame fetches /video/playback, which serves the stored transcode if one
  # exists, else the original — so probe the encoded file when present. The
  # middle field is the SHORTER dimension (min of width,height): Immich's
  # targetResolution scales the short side, so a 720 target yields 1280x720
  # (landscape) or 720x1280 (portrait) — both short-side 720. Checking raw height
  # would perpetually re-flag portrait clips.
  local enc path
  enc="$(enc_path "$1")"
  path="${enc:-$2}"
  kubectl exec -n "$NS" "$POD" -c "$CTR" -- sh -c \
    'FF=$(command -v ffprobe || echo /usr/lib/jellyfin-ffmpeg/ffprobe); "$FF" -v error -select_streams v:0 -show_entries stream=codec_name,width,height:format=bit_rate -of default=noprint_wrappers=1 "'"$path"'" 2>/dev/null' \
    | awk -F= '/^codec_name=/{c=$2} /^width=/{w=$2} /^height=/{h=$2} /^bit_rate=/{b=$2} END{if(b==""||b=="N/A")b=0; if(w==""||w=="N/A")w=0; if(h==""||h=="N/A")h=0; s=(w<h?w:h); if(w==0)s=h; if(h==0)s=w; print c, s, b}'
}

set_policy_via_pr() { # set_policy_via_pr <disabled|bitrate> <subject>
  local target="$1" subject="$2" wt br pr
  # Refresh origin/$MAIN first: the flip PR merged in THIS run, so the local
  # remote-tracking ref is stale — branching the revert off it would diff
  # against the pre-flip tree (empty diff -> failed revert, policy stuck on).
  git -C "$REPO" fetch -q origin "$MAIN"
  # Unique branch per call ($$ alone collides when the EXIT-trap retries the
  # same target after a failed attempt).
  wt="$(mktemp -d)"; br="claude/immich-transcode-${target}-$$-${RANDOM}"
  git -C "$REPO" worktree add -q "$wt" -b "$br" "origin/$MAIN"
  sed -i -E 's/("transcode": )"(disabled|required|bitrate)"/\1"'"$target"'"/' "$wt/$ES_PATH"
  git -C "$wt" add "$ES_PATH"
  git -C "$wt" commit -q -m "$subject" \
    -m "Automated by tools/immich-frame-video-transcode.sh (temporary flip-scope-revert)." \
    -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
  # Push, tolerating concurrent advances of origin/$MAIN. The repo pre-push hook
  # rejects a branch that is behind origin/$MAIN, and main can advance between
  # branch creation and push — the flip PR this run just merged, a Renovate
  # automerge, or a parallel session. That once left the REVERT push failing and
  # policy stuck on 'bitrate' until the EXIT-trap fired. Re-fetch, rebase onto the
  # fresh tip, and retry so the revert lands on the first (not emergency) attempt.
  local attempt
  for attempt in 1 2 3 4; do
    git -C "$wt" push -q -u origin "$br" 2>/dev/null && break
    [ "$attempt" = 4 ] && { git -C "$REPO" worktree remove --force "$wt" 2>/dev/null || true; die "push of $br failed after $attempt attempts (rebasing onto origin/$MAIN each time)"; }
    warn "push rejected (branch likely behind origin/$MAIN) — rebasing and retrying ($attempt/3)"
    git -C "$wt" fetch -q origin "$MAIN"
    git -C "$wt" rebase -q "origin/$MAIN" \
      || { git -C "$wt" rebase --abort 2>/dev/null || true; git -C "$REPO" worktree remove --force "$wt" 2>/dev/null || true; die "rebase of $br onto origin/$MAIN conflicted — resolve manually"; }
  done
  pr="$(gh pr create -R "$ORG_REPO" --head "$br" --title "$subject" \
        --body "Automated flip-scope-revert step — see tools/immich-frame-video-transcode.sh." | tail -1)"
  c "PR $pr — waiting for CI"
  # `gh pr checks --watch` errors out ("no checks reported") if it looks before
  # GitHub has registered any check runs — a race right after `gh pr create`.
  # Wait for at least one check to appear before watching (up to ~5 min).
  for _ in $(seq 1 30); do
    [ -n "$(gh pr checks "$pr" -R "$ORG_REPO" 2>/dev/null)" ] && break
    sleep 10
  done
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

# ---- 1. inventory album videos: probe what the frame ACTUALLY gets served ----
# (no downloads; in-pod ffprobe of the encoded transcode if present, else original)
c "current global policy: $(policy_live)"
c "frame-conforming = h264 AND short-side<=${MAX_SHORT} AND bitrate<=$((MAX_BITRATE_BPS/1000000))Mbps"
declare -a NEED=()          # asset ids whose SERVED stream is not frame-conforming
for alb in "${ALBUMS[@]}"; do
  c "scanning album $alb"
  # NOTE: single search page (<=250 assets). Frame albums are small.
  while IFS=$'\t' read -r id path; do
    [ -n "$id" ] || continue
    read -r codec short bitrate < <(probe_served "$id" "$path")
    : "${codec:=?}" "${short:=0}" "${bitrate:=0}"
    mbps=$(( bitrate / 1000000 ))
    if [ "$codec" = "h264" ] && [ "$short" -le "$MAX_SHORT" ] && [ "$bitrate" -le "$MAX_BITRATE_BPS" ]; then
      c "  ok      $id  (${codec} ${short}p ~${mbps}Mbps)"
    else
      c "  needs   $id  (${codec} ${short}p ~${mbps}Mbps)"
      NEED+=("$id")
    fi
  done < <(api POST /api/search/metadata "{\"albumIds\":[\"$alb\"],\"type\":\"VIDEO\"}" \
             | jq -r '.assets.items[]? | .id + "\t" + .originalPath')
done

if [ ${#NEED[@]} -eq 0 ]; then
  c "all album videos already serve frame-conforming h264 — nothing to do. Done."
  exit 0
fi
c "${#NEED[@]} video(s) need a (re-)transcode: ${NEED[*]}"

if [ "$ASSUME_YES" != 1 ]; then
  printf '\nThis will temporarily flip Immich transcode policy to "bitrate" (cap: 720p / 5 Mbps) and RESTART %s TWICE. Proceed? [y/N] ' "$STS"
  read -r ans; [ "$ans" = y ] || [ "$ans" = Y ] || die "aborted by user"
fi

# ---- 2. baseline, flip, transcode, leak-check ----
mapfile -t BASELINE < <(encoded_ids)
c "baseline: ${#BASELINE[@]} assets already have an encoded video"

set_policy_via_pr bitrate "chore(immich): temporarily cap frame-album video (720p/5Mbps)"
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

# ---- 4. verify each targeted asset now serves frame-conforming h264 ----
c "verifying targeted assets serve h264 short-side<=${MAX_SHORT}p <=$((MAX_BITRATE_BPS/1000000))Mbps"
ok=1
for id in "${NEED[@]}"; do
  enc="$(enc_path "$id")"
  if [ -z "$enc" ]; then warn "  $id: NO encoded file produced"; ok=0; continue; fi
  read -r codec short bitrate < <(probe_served "$id" "")
  : "${codec:=?}" "${short:=0}" "${bitrate:=0}"
  mbps=$(( bitrate / 1000000 ))
  if [ "$codec" = "h264" ] && [ "$short" -le "$MAX_SHORT" ] && [ "$bitrate" -le "$MAX_BITRATE_BPS" ]; then
    c "  $id: encoded ${codec} ${short}p ~${mbps}Mbps ✓"
  else
    warn "  $id: encoded ${codec} ${short}p ~${mbps}Mbps (want h264 short-side<=${MAX_SHORT}p <=$((MAX_BITRATE_BPS/1000000))Mbps)"; ok=0
  fi
done

c "final global policy: $(policy_live)"
[ "$ok" = 1 ] && c "DONE — frame-album videos are h264-playable; global policy back to disabled." \
             || die "one or more targeted assets did not end up h264 — investigate."
