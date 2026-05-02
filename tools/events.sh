#!/usr/bin/env bash
# Watch cluster events with the chatty Flux heartbeat events filtered out.
#
# Defaults: all namespaces, watch new events only, filter out Flux's
# normal-state events (ArtifactUpToDate, ReconciliationSucceeded, etc.)
# that otherwise bury everything actionable.
#
# Usage:
#   ./events.sh                       # all namespaces, default filter
#   NAMESPACE=foo ./events.sh         # scope to a single namespace
#   WARNINGS_ONLY=1 ./events.sh       # only type=Warning events
#   SHOW_FILTERED=1 ./events.sh       # invert: show ONLY the events the
#                                     # default filter normally hides
#                                     # (audit what we're ignoring)
#   EVENTS_FILTER_EXTRA='Foo|Bar' \
#     ./events.sh                     # add to the default filter regex

set -uo pipefail

# Flux's normal-state heartbeat events. These fire on every reconcile
# loop and bury anything actionable when watching all namespaces.
FILTER='ArtifactUpToDate|ReconciliationSucceeded|GitOperationSucceeded|DependencyNotReady'
if [[ -n "${EVENTS_FILTER_EXTRA:-}" ]]; then
  FILTER="${FILTER}|${EVENTS_FILTER_EXTRA}"
fi

ns_args=(-A)
if [[ -n "${NAMESPACE:-}" ]]; then
  ns_args=(-n "$NAMESPACE")
fi

extra_args=()
if [[ "${WARNINGS_ONLY:-0}" == "1" ]]; then
  extra_args+=(--field-selector type=Warning)
fi

# SHOW_FILTERED=1 inverts grep so you see only the events the default
# filter normally hides — useful periodic audit of "is anything in the
# noise list actually worth looking at?"
grep_flag='-vE'
if [[ "${SHOW_FILTERED:-0}" == "1" ]]; then
  grep_flag='-E'
fi

kubectl get events "${ns_args[@]}" "${extra_args[@]}" --watch-only \
  | grep --line-buffered "$grep_flag" "$FILTER"
