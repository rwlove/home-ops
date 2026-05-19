#!/bin/sh
# chrome-smoke-sweep — nightly browser smoke test of public-facing apps.
# Talks directly to chrome-mcp via the in-namespace Service (no broker /
# no JWT — intra-namespace traffic is allowed via the baseline CNP).
#
# MVP: navigates each hostname, captures title + console error count, logs
# results. Exits non-zero if any host fails to return a final URL (proxy
# for "browser couldn't load anything"). No baseline-diff yet — that's a
# follow-up; this run just makes the data visible in kubectl logs.

set -eu

PW_URL="${PW_URL:-http://chrome-mcp.mcp-system.svc.cluster.local:8931/mcp}"
HOSTS_FILE="${HOSTS_FILE:-/config/hostnames.txt}"

HEADERS_FILE="$(mktemp)"
trap 'rm -f "${HEADERS_FILE}"' EXIT

mcp_post() {
  # $1 = JSON body. Captures response headers to HEADERS_FILE (overwrite).
  # Handles both plain-JSON and SSE response framing: if the body has a
  # `data: ` line, emit just that payload; otherwise emit the body as-is.
  RAW="$(curl -fsS -X POST "${PW_URL}" \
    -D "${HEADERS_FILE}" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    ${SESSION_ID:+-H "Mcp-Session-Id: ${SESSION_ID}"} \
    --data "$1")"
  SSE_LINE="$(printf '%s' "${RAW}" | sed -n '/^data: /{s///;p;q;}')"
  if [ -n "${SSE_LINE}" ]; then
    printf '%s' "${SSE_LINE}"
  else
    printf '%s' "${RAW}"
  fi
}

mcp_notify() {
  # Notifications never get a response body; suppress.
  curl -fsS -X POST "${PW_URL}" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -H "Mcp-Session-Id: ${SESSION_ID}" \
    --data "$1" \
    >/dev/null
}

echo "== chrome-smoke-sweep =="
echo "PW_URL=${PW_URL}"
echo "HOSTS_FILE=${HOSTS_FILE}"
echo

SESSION_ID=""
INIT_RESP="$(mcp_post '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"chrome-smoke-sweep","version":"1.0"}}}')"
SESSION_ID="$(awk 'BEGIN{IGNORECASE=1} /^mcp-session-id:/{print $2}' "${HEADERS_FILE}" | tr -d '\r\n')"

if [ -z "${SESSION_ID}" ]; then
  echo "FATAL: no Mcp-Session-Id header from chrome-mcp" >&2
  cat "${HEADERS_FILE}" >&2
  exit 2
fi

# Don't print the full SESSION_ID — it's a per-invocation token, not secret
# in the traditional sense but no reason to leak it to logs either.
echo "Session established."
echo

mcp_notify '{"jsonrpc":"2.0","method":"notifications/initialized"}'

REQ_ID=10
TOTAL=0
FAILED=0
HIGH_CONSOLE=0
printf '%-9s  %-45s  %s\n' "STATUS" "HOSTNAME" "TITLE / FINAL URL / CONSOLE"

while IFS= read -r LINE; do
  HOST="$(printf '%s' "${LINE}" | sed -e 's/[[:space:]]*$//' -e 's/^[[:space:]]*//')"
  [ -z "${HOST}" ] && continue
  case "${HOST}" in \#*) continue ;; esac

  TOTAL=$((TOTAL + 1))
  REQ_ID=$((REQ_ID + 1))
  BODY="$(printf '{"jsonrpc":"2.0","id":%d,"method":"tools/call","params":{"name":"browser_navigate","arguments":{"url":"https://%s"}}}' "${REQ_ID}" "${HOST}")"
  RESP="$(mcp_post "${BODY}" || echo '{}')"

  # Retry once on the known "Session not found" intermittency
  # (see project_chrome_mcp_playwright_gotchas).
  if printf '%s' "${RESP}" | grep -q "Session not found"; then
    REQ_ID=$((REQ_ID + 1))
    BODY="$(printf '{"jsonrpc":"2.0","id":%d,"method":"tools/call","params":{"name":"browser_navigate","arguments":{"url":"https://%s"}}}' "${REQ_ID}" "${HOST}")"
    RESP="$(mcp_post "${BODY}" || echo '{}')"
  fi

  TEXT="$(printf '%s' "${RESP}" | jq -r '.result.content[]?.text // empty' 2>/dev/null | tr -s '\n' ' ')"

  TITLE="$(printf '%s' "${TEXT}" | sed -n 's/.*Page Title: \([^#]*\) #*.*/\1/p' | sed 's/ *$//' | head -c 60)"
  FINAL_URL="$(printf '%s' "${TEXT}" | sed -n 's/.*Page URL: \([^ ]*\) .*/\1/p' | head -1)"
  ERRORS="$(printf '%s' "${TEXT}" | sed -n 's/.*Console: \([0-9]*\) errors.*/\1/p' | head -1)"
  ERRORS="${ERRORS:-0}"

  STATUS="OK"
  if [ -z "${FINAL_URL}" ]; then
    STATUS="FAIL"
    FAILED=$((FAILED + 1))
  elif [ "${ERRORS}" -gt 15 ]; then
    # Threshold matches what an unauthenticated SPA login page (Windmill,
    # etc.) typically emits — see memory. Above this is a probable
    # regression worth eyeballing.
    STATUS="LOUD"
    HIGH_CONSOLE=$((HIGH_CONSOLE + 1))
  fi

  printf '%-9s  %-45s  %s | %s | %s errors\n' "${STATUS}" "${HOST}" "${TITLE:-<no title>}" "${FINAL_URL:-<no url>}" "${ERRORS}"
done < "${HOSTS_FILE}"

echo
echo "Summary: ${TOTAL} hosts | ${FAILED} failed | ${HIGH_CONSOLE} loud-console"

# Exit non-zero only on hard navigate failures. LOUD is a signal for the
# operator to look, not a CronJob failure — the cluster has plenty of
# legitimate "noisy console on the login page" cases.
[ "${FAILED}" -eq 0 ]
