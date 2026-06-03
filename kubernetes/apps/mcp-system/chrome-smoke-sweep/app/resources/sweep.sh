#!/bin/sh
# chrome-smoke-sweep — nightly browser smoke test of public-facing apps.
# Talks directly to chrome-mcp via the in-namespace Service (no broker /
# no JWT — intra-namespace traffic is allowed via the baseline CNP).
#
# Flux postBuild eats unescaped ${VAR} — every shell variable below uses
# $${VAR} so it survives substitution and the in-pod script sees ${VAR}.
# (See feedback_flux_postbuild_shell_vars.)

set -eu

PW_URL="$${PW_URL:-http://chrome-mcp.mcp-system.svc.cluster.local:8931/mcp}"
HOSTS_FILE="$${HOSTS_FILE:-/config/hostnames.txt}"

HEADERS_FILE="$(mktemp)"
trap 'rm -f "$${HEADERS_FILE}"' EXIT

mcp_post() {
  # $1 = JSON body. Captures response headers to HEADERS_FILE (overwrite).
  # Handles both plain-JSON and SSE response framing: if the body has a
  # `data: ` line, emit just that payload; otherwise emit the body as-is.
  #
  # --max-time 45 — Playwright MCP returns text/event-stream that may not
  # close cleanly; bound every call so a hung navigation doesn't wedge
  # the whole sweep. (First iteration hung after ~5 hosts in a 20-min Job.)
  RAW="$(curl -fsS --max-time 45 -X POST "$${PW_URL}" \
    -D "$${HEADERS_FILE}" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    $${SESSION_ID:+-H "Mcp-Session-Id: $${SESSION_ID}"} \
    --data "$1" || true)"
  SSE_LINE="$(printf '%s' "$${RAW}" | sed -n '/^data: /{s///;p;q;}')"
  if [ -n "$${SSE_LINE}" ]; then
    printf '%s' "$${SSE_LINE}"
  else
    printf '%s' "$${RAW}"
  fi
}

mcp_notify() {
  curl -fsS --max-time 10 -X POST "$${PW_URL}" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -H "Mcp-Session-Id: $${SESSION_ID}" \
    --data "$1" \
    >/dev/null || true
}

mcp_init() {
  # Establish a fresh MCP session and set SESSION_ID. Playwright MCP's
  # session goes stale after a handful of tool calls (404 on subsequent
  # requests), so we re-init per host. Cost is one extra round-trip,
  # ~200ms.
  SESSION_ID=""
  mcp_post '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"chrome-smoke-sweep","version":"1.0"}}}' >/dev/null
  SESSION_ID="$(awk 'BEGIN{IGNORECASE=1} /^mcp-session-id:/{print $2}' "$${HEADERS_FILE}" | tr -d '\r\n')"
  [ -z "$${SESSION_ID}" ] && return 1
  mcp_notify '{"jsonrpc":"2.0","method":"notifications/initialized"}'
  return 0
}

echo "== chrome-smoke-sweep =="
echo "PW_URL=$${PW_URL}"
echo "HOSTS_FILE=$${HOSTS_FILE}"
echo

# Sanity check — fail fast if chrome-mcp won't even hand us a session.
if ! mcp_init; then
  echo "FATAL: chrome-mcp won't issue Mcp-Session-Id on initial init" >&2
  cat "$${HEADERS_FILE}" >&2
  exit 2
fi
echo "Initial session established."
echo

REQ_ID=10
TOTAL=0
FAILED=0
HIGH_CONSOLE=0
printf '%-9s  %-45s  %s\n' "STATUS" "HOSTNAME" "TITLE / FINAL URL / CONSOLE"

while IFS= read -r LINE; do
  HOST="$(printf '%s' "$${LINE}" | sed -e 's/[[:space:]]*$//' -e 's/^[[:space:]]*//')"
  [ -z "$${HOST}" ] && continue
  case "$${HOST}" in \#*) continue ;; esac

  TOTAL=$((TOTAL + 1))

  # Navigate with one retry. A single transient blip — a stale MCP
  # session, a slow OIDC redirect, or a 45s nav timeout — shouldn't fail
  # the whole sweep; only a host unreachable on BOTH attempts counts as
  # FAIL. KubeJobFailed on a once-daily Job pages, so one 5am hiccup on
  # one of N hosts must not trip it. (Mirrors the lidarr-sab-autoimport
  # in-script retry fix, PR #12203.)
  ATTEMPT=0
  STATUS="FAIL"
  TITLE=""
  FINAL_URL=""
  ERRORS=0
  while [ "$${ATTEMPT}" -lt 2 ]; do
    ATTEMPT=$((ATTEMPT + 1))

    # Re-init per attempt. Avoids the stale-session 404s seen after ~5 calls.
    if ! mcp_init; then
      TITLE="<session init failed>"
      if [ "$${ATTEMPT}" -lt 2 ]; then
        sleep 3
        continue
      fi
      break
    fi

    REQ_ID=$((REQ_ID + 1))
    BODY="$(printf '{"jsonrpc":"2.0","id":%d,"method":"tools/call","params":{"name":"browser_navigate","arguments":{"url":"https://%s"}}}' "$${REQ_ID}" "$${HOST}")"
    RESP="$(mcp_post "$${BODY}")"

    TEXT="$(printf '%s' "$${RESP}" | jq -r '.result.content[]?.text // empty' 2>/dev/null)"

    TITLE="$(printf '%s\n' "$${TEXT}" | sed -n 's/^- Page Title: //p' | head -1 | cut -c1-60)"
    FINAL_URL="$(printf '%s\n' "$${TEXT}" | sed -n 's/^- Page URL: //p' | head -1)"
    ERRORS="$(printf '%s\n' "$${TEXT}" | sed -n 's/^- Console: \([0-9]*\) errors.*/\1/p' | head -1)"
    ERRORS="$${ERRORS:-0}"

    if [ -n "$${FINAL_URL}" ]; then
      STATUS="OK"
      break
    fi
    # Unreachable this attempt; back off briefly before the retry.
    if [ "$${ATTEMPT}" -lt 2 ]; then
      sleep 3
    fi
  done

  if [ "$${STATUS}" = "FAIL" ]; then
    FAILED=$((FAILED + 1))
  elif [ "$${ERRORS}" -gt 15 ]; then
    STATUS="LOUD"
    HIGH_CONSOLE=$((HIGH_CONSOLE + 1))
  fi

  printf '%-9s  %-45s  %s | %s | %s errors\n' "$${STATUS}" "$${HOST}" "$${TITLE:-<no title>}" "$${FINAL_URL:-<no url>}" "$${ERRORS}"
done < "$${HOSTS_FILE}"

echo
echo "Summary: $${TOTAL} hosts | $${FAILED} failed | $${HIGH_CONSOLE} loud-console"

[ "$${FAILED}" -eq 0 ]
