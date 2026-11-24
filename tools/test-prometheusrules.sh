#!/usr/bin/env bash
# Validate every PrometheusRule with promtool, and run the unit tests.
#
# 1. `promtool check rules` on the `.spec` of EVERY `kind: PrometheusRule`
#    document under kubernetes/ (override the search root with $1), whatever
#    the file is called. Most alerts live in addons/alerts/<name>.yaml or
#    prometheus-rule.yaml, not *prometheusrule.yaml.
# 2. `promtool test rules` for every `*prometheusrule.test.yaml`. Each test's
#    sibling rule file is the same name without `.test`
#    (`prometheusrule.test.yaml` -> `prometheusrule.yaml`). The rule is a
#    PrometheusRule CR, but promtool reads plain rule files, so `.spec` is
#    extracted to `rules.generated.yaml` next to a copy of the test in a
#    scratch dir — test files therefore declare
#    `rule_files: [rules.generated.yaml]`.
#
# Adding a rule or a test needs no CI edit: drop
# `<name>prometheusrule.test.yaml` next to `<name>prometheusrule.yaml`.
# Wired into .github/workflows/promtool.yaml.
#
# Requires yq (mikefarah) and docker or podman. promtool comes from the
# Prometheus image below; one container serves the whole run.

set -euo pipefail

# renovate: datasource=docker depName=quay.io/prometheus/prometheus
PROMETHEUS_VERSION="v3.5.0"
IMAGE="quay.io/prometheus/prometheus:${PROMETHEUS_VERSION}"

root="${1:-kubernetes}"

command -v yq >/dev/null || { echo "yq (mikefarah) is required" >&2; exit 2; }
if command -v docker >/dev/null; then rt=docker
elif command -v podman >/dev/null; then rt=podman
else echo "docker or podman is required" >&2; exit 2; fi

work="$(mktemp -d)"
name="promtool-rules-$$"
cleanup() {
  "$rt" rm -f "$name" >/dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

fail=0

# Pass 1 staging: every PrometheusRule document. `^kind:` (not indented)
# skips CRs embedded as strings in HelmRelease values; resources/ holds app
# payloads, not manifests.
mkdir -p "$work/check"
mapfile -t rulefiles < <(grep -rlE '^kind:[[:space:]]+PrometheusRule[[:space:]]*$' --include='*.yaml' "$root" \
  | grep -v '/resources/' | grep -v '\.test\.yaml$' | sort)
n=0
declare -A source_of
for f in "${rulefiles[@]}"; do
  # One output file per PrometheusRule document in $f.
  while IFS= read -r idx; do
    yq "select(document_index == ${idx}) | .spec" "$f" > "$work/check/rules-$n.yaml"
    source_of[$n]="$f|$idx"
    n=$((n + 1))
  done < <(yq 'select(.kind == "PrometheusRule") | document_index' "$f")
done

# Pass 2 staging: the unit tests.
mapfile -t tests < <(find "$root" -type f -name '*prometheusrule.test.yaml' | sort)
declare -a test_dirs=()
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
  d="test/$(printf '%s' "$t" | tr '/' '_')"
  mkdir -p "$work/$d"
  yq '.spec' "$rule" > "$work/$d/rules.generated.yaml"
  cp "$t" "$work/$d/test.yaml"
  test_dirs+=("$d|$t")
done

if [[ $n -eq 0 && ${#test_dirs[@]} -eq 0 ]]; then
  echo "no PrometheusRule documents or tests under $root; nothing to do"
  exit "$fail"
fi

chmod -R a+rX "$work"
if ! "$rt" run -d --name "$name" --entrypoint sleep -v "$work:/t:ro,Z" "$IMAGE" 1800 >/dev/null; then
  echo "::error::could not start $IMAGE"
  exit 2
fi

for ((i = 0; i < n; i++)); do
  if ! out=$("$rt" exec "$name" promtool check rules "/t/check/rules-$i.yaml" 2>&1); then
    src="${source_of[$i]}"
    echo "::error file=${src%%|*}::promtool check rules failed (document ${src##*|})"
    echo "$out"
    fail=1
  fi
done
echo "promtool check rules: ${n} PrometheusRule document(s) from ${#rulefiles[@]} file(s)"

for entry in "${test_dirs[@]}"; do
  d="${entry%%|*}"
  t="${entry#*|}"
  echo "== $t"
  "$rt" exec "$name" promtool test rules "/t/$d/test.yaml" || fail=1
done
echo "promtool test rules: ${#test_dirs[@]} test file(s)"

exit "$fail"
