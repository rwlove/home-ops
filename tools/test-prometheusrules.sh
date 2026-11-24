#!/usr/bin/env bash
# Run promtool unit tests for every PrometheusRule that has one.
#
# Discovers every `*prometheusrule.test.yaml` under kubernetes/ (override the
# search root with $1). Each test's sibling rule file is the same name without
# `.test` (`prometheusrule.test.yaml` -> `prometheusrule.yaml`). The rule is a
# PrometheusRule CR, but promtool reads plain rule files, so `.spec` is
# extracted to `rules.generated.yaml` next to a copy of the test in a scratch
# dir — test files therefore declare `rule_files: [rules.generated.yaml]`.
# For each pair it runs `promtool check rules` then `promtool test rules`.
#
# Adding a test needs no CI edit: drop `<name>prometheusrule.test.yaml` next
# to `<name>prometheusrule.yaml`. Wired into .github/workflows/promtool.yaml.
#
# Requires yq (mikefarah) and docker or podman. promtool comes from the
# Prometheus image below.

set -euo pipefail

# renovate: datasource=docker depName=quay.io/prometheus/prometheus
PROMETHEUS_VERSION="v3.5.0"
IMAGE="quay.io/prometheus/prometheus:${PROMETHEUS_VERSION}"

root="${1:-kubernetes}"

command -v yq >/dev/null || { echo "yq (mikefarah) is required" >&2; exit 2; }
if command -v docker >/dev/null; then rt=docker
elif command -v podman >/dev/null; then rt=podman
else echo "docker or podman is required" >&2; exit 2; fi

mapfile -t tests < <(find "$root" -type f -name '*prometheusrule.test.yaml' | sort)
if [[ ${#tests[@]} -eq 0 ]]; then
  echo "no *prometheusrule.test.yaml found under $root; nothing to do"
  exit 0
fi

work="$(mktemp -d)"
name="promtool-rules-$$"
cleanup() {
  "$rt" rm -f "$name" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

fail=0
for t in "${tests[@]}"; do
  rule="${t%.test.yaml}.yaml"
  if [[ ! -f "$rule" ]]; then
    echo "::error file=$t::sibling rule file $rule not found"
    fail=1
    continue
  fi
  if ! grep -qE '^\s*-\s*rules\.generated\.yaml\s*$' "$t"; then
    echo "::error file=$t::rule_files must be [rules.generated.yaml] (the extracted .spec)"
    fail=1
    continue
  fi
  d="$work/$(printf '%s' "$t" | tr '/' '_')"
  mkdir -p "$d"
  yq '.spec' "$rule" > "$d/rules.generated.yaml"
  cp "$t" "$d/test.yaml"
  chmod 755 "$d"
  chmod 644 "$d"/*
  echo "== $t (rules: $rule)"
  if ! "$rt" run -d --name "$name" --entrypoint sleep -v "$d:/t:ro,Z" "$IMAGE" 600 >/dev/null; then
    echo "::error::could not start $IMAGE"
    exit 2
  fi
  "$rt" exec "$name" promtool check rules /t/rules.generated.yaml || fail=1
  "$rt" exec "$name" promtool test rules /t/test.yaml || fail=1
  "$rt" rm -f "$name" >/dev/null
done

exit "$fail"
