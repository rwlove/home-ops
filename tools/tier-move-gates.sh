#!/usr/bin/env bash
# Gates for moving a dir between Flux tier parents (docs/src/gitops_layout.md,
# "Moving a dir between parents"). Read-only against the live cluster.
#
# Usage (from a checkout at the revision under test):
#   tools/tier-move-gates.sh baseline            # before the window opens
#   tools/tier-move-gates.sh union               # step 3: render union == baseline
#   tools/tier-move-gates.sh move <dest> <src>   # step 4, after dest -> src -> dest
#   tools/tier-move-gates.sh gen                 # step 4: no child generation bumps
#   tools/tier-move-gates.sh owner               # step 4: owner labels name the inventory's parent
#   tools/tier-move-gates.sh gcwatch [since]     # step 4: no GC deletions logged
#   tools/tier-move-gates.sh leak <parent>       # step 5
#   tools/tier-move-gates.sh flip-pre <rev>      # step 6 precondition (rev = the move commit)
#   tools/tier-move-gates.sh health              # essential infra + Flux not-ready
#   tools/tier-move-gates.sh postmerge <rev>     # CI on main + ARC runners for a merge commit
#   tools/tier-move-gates.sh freeze-prs          # open PRs touching the tier roots (re-run their Lint)
#
# Parents and their roots come from kubernetes/flux/cluster/ks.yaml (the
# home-ops.io/tier-parent label and spec.path), so a new tier needs no edit here.
# State (the baseline) lives in $GATE_STATE.
set -euo pipefail
export LC_ALL=C
G=${GATE_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/home-ops/tier-move-gates}
mkdir -p "$G"
KS_FILE=kubernetes/flux/cluster/ks.yaml
WORKLOAD_KINDS='Deployment|DaemonSet|StatefulSet|CronJob|HelmRelease'

fail() { echo "GATE FAIL: $*" >&2; exit 1; }
[ -f "$KS_FILE" ] || fail "run from the repo root ($KS_FILE not found)"

# Parents in Git (render side) and live (inventory side).
git_parents() { yq -N -r 'select(.metadata.labels["home-ops.io/tier-parent"] == "true") | .metadata.name' "$KS_FILE"; }
live_parents() { kubectl -n flux-system get ks -l home-ops.io/tier-parent=true -o name | sed 's#.*/##'; }
all_parents() { { git_parents; live_parents; } | sort -u; }
root_of() { yq -N -r "select(.metadata.name == \"$1\") | .spec.path" "$KS_FILE" | sed 's#^\./##'; }
roots() { for p in $(git_parents); do root_of "$p"; done; }

# Inventory IDs as Flux writes them: <ns>_<name>_<group>_<kind>.
toinv() { kubectl kustomize "$1" | yq -o=json -I=0 . | jq -r 'select(.kind)|"\(.metadata.namespace//"")_\(.metadata.name|gsub(":";"__"))_\(.apiVersion|split("/")|if length==2 then .[0] else "" end)_\(.kind)"' | sort; }
inv() { { kubectl -n flux-system get ks "$1" -o json 2>/dev/null || echo '{}'; } | jq -r '.status.inventory.entries[]?.id' | sort; }
ks_gens() { kubectl get ks -A -o json | jq -r '.items[]|"\(.metadata.namespace)/\(.metadata.name) \(.metadata.generation)"' | sort; }
leak_scan() { # $1 parent -> labelled objects with no controller owner, as inventory IDs; error count on fd 3
  local errs=0 r
  { # the count must be written inside the pipeline's subshell
    for r in $(kubectl api-resources --verbs=list -o name); do
      kubectl get "$r" -A -l "kustomize.toolkit.fluxcd.io/name=$1,kustomize.toolkit.fluxcd.io/namespace=flux-system" -o json 2>/dev/null || errs=$((errs + 1))
    done
    echo "$errs" >&3
  } | jq -r '.items[]?|select([.metadata.ownerReferences[]?|select(.controller==true)]|length==0)|"\(.metadata.namespace//"")_\(.metadata.name|gsub(":";"__"))_\(.apiVersion|split("/")|if length==2 then .[0] else "" end)_\(.kind)"' | sort -u
}
need_baseline() { [ -s "$G/baseline-union.txt" ] || fail "no baseline in $G; run '$0 baseline' before the window opens"; }

case ${1:-} in
baseline)
  for p in $(all_parents); do inv "$p"; done | sort -u > "$G/baseline-union.txt"
  ks_gens > "$G/gen-ks.txt"
  # Workloads the parents own directly (not through a child KS).
  for p in $(all_parents); do inv "$p"; done | awk -F_ -v k="^($WORKLOAD_KINDS)\$" '$NF ~ k' | sort -u |
    while IFS=_ read -r ns name grp kind; do
      echo "$ns/$kind${grp:+.$grp}/$name $(kubectl -n "$ns" get "$kind${grp:+.$grp}" "$name" -o jsonpath='{.metadata.generation}')"
    done > "$G/gen-wl.txt"
  for p in $(all_parents); do leak_scan "$p" 3>"$G/leak-errs-$p.txt" > /dev/null; done
  wc -l "$G/baseline-union.txt" "$G/gen-ks.txt" "$G/gen-wl.txt" ;;
union)
  need_baseline
  for r in $(roots); do toinv "$r"; done | sort > "$G/union.txt"
  dup=$(uniq -d "$G/union.txt")
  [ -z "$dup" ] || fail "duplicate IDs across roots: $(head -5 <<< "$dup")"
  diff "$G/union.txt" "$G/baseline-union.txt" || fail "render union != baseline"
  echo "union OK ($(wc -l < "$G/union.txt") objects, roots: $(roots | tr '\n' ' '))" ;;
move)
  need_baseline
  d=${2:?destination parent}; s=${3:?source parent}
  diff <(toinv "$(root_of "$d")") <(inv "$d") || fail "$d inventory != its render"
  diff <(toinv "$(root_of "$s")") <(inv "$s") || fail "$s inventory != its render"
  [ -z "$(comm -12 <(inv "$d") <(inv "$s"))" ] || fail "objects in both $d and $s inventories"
  for p in $(live_parents); do inv "$p"; done | sort > "$G/live-union.txt"
  [ -z "$(uniq -d "$G/live-union.txt")" ] || fail "object in two parents' inventories"
  diff "$G/live-union.txt" "$G/baseline-union.txt" || fail "live inventory union != baseline"
  echo "move $s -> $d OK" ;;
gen)
  need_baseline
  # The parents themselves legitimately change; compare children only.
  bad=$(join "$G/gen-ks.txt" <(ks_gens) | awk '$2!=$3 && $1!~/^flux-system\/cluster-/')
  [ -z "$bad" ] || fail "child KS generation bumped: $bad"
  while read -r w g; do
    IFS=/ read -r ns res name <<< "$w"
    now=$(kubectl -n "$ns" get "$res" "$name" -o jsonpath='{.metadata.generation}')
    [ "$now" = "$g" ] || fail "$w generation $g -> $now"
  done < "$G/gen-wl.txt"
  echo "generations OK" ;;
owner)
  : > "$G/owner-bad.txt"
  for p in $(live_parents); do
    inv "$p" > "$G/inv-$p.txt"
    sed -E 's/^.*_([^_]*)_([^_]+)$/\2 \1/' "$G/inv-$p.txt" | sort -u | while read -r kind grp; do
      kubectl get "$kind${grp:+.$grp}" -A -o json 2>/dev/null | jq -r --arg g "$grp" --arg k "$kind" \
        '.items[]|"\(.metadata.namespace//"")_\(.metadata.name|gsub(":";"__"))_\($g)_\($k) \(.metadata.labels["kustomize.toolkit.fluxcd.io/name"]//"NONE")"'
    done | sort > "$G/labels-$p.txt"
    # The flux-system Namespace is shared by every parent and labelled by the last to apply.
    join -a1 -e MISSING -o 0,2.2 "$G/inv-$p.txt" "$G/labels-$p.txt" |
      awk -v p="$p" '$1!="_flux-system__Namespace" && $2!=p {print p": "$0}' >> "$G/owner-bad.txt"
  done
  [ ! -s "$G/owner-bad.txt" ] || fail "owner labels wrong: $(head -5 "$G/owner-bad.txt")"
  echo "owner labels OK" ;;
leak)
  p=${2:?parent}
  leak_scan "$p" 3>"$G/leak-errs-now.txt" > "$G/leak-$p.txt"
  errs=$(cat "$G/leak-errs-now.txt"); allow=$(cat "$G/leak-errs-$p.txt" 2>/dev/null || echo 0)
  [ "$errs" -le "$allow" ] || fail "leak scan: $errs resource types errored (baseline $allow)"
  extra=$(comm -23 "$G/leak-$p.txt" <(inv "$p"))
  [ -z "$extra" ] || fail "$p leaks (labelled, not in its inventory): $extra"
  echo "leak $p OK" ;;
flip-pre)
  rev=$(git rev-parse --verify "${2:?the move commit}^{commit}")
  for p in $(live_parents); do
    applied=$(kubectl -n flux-system get ks "$p" -o jsonpath='{.status.lastAppliedRevision}')
    git merge-base --is-ancestor "$rev" "${applied##*:}" || fail "$p at $applied, not at or after $rev"
    diff <(toinv "$(root_of "$p")") <(inv "$p") > /dev/null || fail "$p inventory != its render"
  done
  "$0" owner
  for p in $(live_parents); do "$0" leak "$p"; done
  echo "flip-pre OK: safe to set prune: true" ;;
health)
  nr=$(kubectl get ks,hr -A -o json | jq '[.items[]|select(.status.conditions[]?|select(.type=="Ready" and .status!="True"))]|length')
  echo "not-ready KS+HR: $nr"
  kubectl get --raw /readyz > /dev/null || fail "apiserver /readyz"
  for d in kube-system/ds/cilium kube-system/deploy/coredns external-secrets/deploy/external-secrets \
    rook-ceph/deploy/rook-ceph-operator longhorn-system/ds/longhorn-manager auth/deploy/authelia; do
    IFS=/ read -r ns k n <<< "$d"
    kubectl -n "$ns" rollout status "$k/$n" --timeout=10s > /dev/null 2>&1 || fail "$d not rolled out"
  done
  kubectl -n rook-ceph get cephcluster -o jsonpath='{.items[0].status.ceph.health}' | grep -q HEALTH_OK || echo "WARN ceph not HEALTH_OK"
  [ "$(kubectl get clusters.postgresql.cnpg.io -A -o json | jq '[.items[]|select(.status.phase!="Cluster in healthy state")]|length')" = 0 ] || fail "CNPG cluster unhealthy"
  echo "health OK" ;;
gcwatch)
  n=$(kubectl -n flux-system logs deploy/kustomize-controller -c manager --since="${2:-20m}" | grep -c '":"deleted"' || true)
  [ "$n" = 0 ] || fail "kustomize-controller logged $n deletion lines"
  echo "no GC deletions in ${2:-20m}" ;;
postmerge)
  # gh run list --commit matches only the full 40-char SHA; a short one returns
  # nothing, so resolve it first.
  sha=$(git rev-parse --verify "${2:?merge commit}^{commit}")
  deadline=$(($(date +%s) + ${POSTMERGE_TIMEOUT:-3600}))
  runs() { gh run list --commit "$sha" --json name,status,conclusion; }
  until r=$(runs) && [ "$(jq length <<< "$r")" -gt 0 ] && [ "$(jq '[.[]|select(.status!="completed")]|length' <<< "$r")" = 0 ]; do
    [ "$(date +%s)" -lt "$deadline" ] || fail "CI on main for $sha not finished after ${POSTMERGE_TIMEOUT:-3600}s: $(jq -c 'map(select(.status!="completed").name)' <<< "$r")"
    pend=$(kubectl -n actions-runner-system get pods --no-headers 2>/dev/null | awk '$3=="Pending"' | wc -l)
    [ "$pend" = 0 ] || echo "WARN $pend ARC runner pods Pending"
    sleep 30
  done
  bad=$(jq -r '[.[]|select(.conclusion!="success" and .conclusion!="skipped" and .conclusion!="cancelled")|"\(.name)=\(.conclusion)"]|join(" ")' <<< "$r")
  [ -z "$bad" ] || fail "CI on main for $sha: $bad"
  c=$(jq -r '[.[]|select(.conclusion=="cancelled").name]|join(", ")' <<< "$r")
  [ -z "$c" ] || echo "WARN cancelled (superseded by a later push to main; that push must pass): $c"
  echo "CI on main OK for $sha: $(jq -r 'map(.name)|join(", ")' <<< "$r")"
  for pfx in actions-runner-controller arc-runner-set-home-ops-.*-listener arc-runner-set-containers-.*-listener; do
    kubectl -n actions-runner-system get pods --no-headers | grep -E "^$pfx" | grep -q ' Running ' || fail "ARC $pfx not Running"
  done
  echo "postmerge OK" ;;
freeze-prs)
  # Lint that passed before the window opened stays green (branch protection
  # is not strict). Re-running it re-reads the window state from main.
  gh pr list --state open --limit 200 --json number,headRefName,title,files \
    --jq '.[]|select([.files[].path|test("^kubernetes/(apps|tiers)/")]|any)|"\(.number)\t\(.headRefName)\t\(.title)"' |
    while IFS=$'\t' read -r n ref title; do
      id=$(gh run list --workflow lint.yaml --branch "$ref" --limit 1 --json databaseId --jq '.[0].databaseId // empty')
      echo "#$n $title"
      if [ -n "$id" ]; then echo "  gh run rerun $id"; else echo "  no Lint run found; push to the branch to trigger one"; fi
    done ;;
*)
  sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'
  exit 2 ;;
esac
