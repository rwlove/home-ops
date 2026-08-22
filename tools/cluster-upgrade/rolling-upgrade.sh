#!/usr/bin/env bash
# ============================================================================
# Rolling kubeadm k8s minor-version upgrade — reusable orchestrator
# ============================================================================
# Distilled from the 2026-08-22 v1.35.4 -> v1.36.4 run. Drives the whole fleet
# one node at a time with hard safety gates; idempotent (skips nodes already at
# target) so it is safe to stop and relaunch. Any gate anomaly calls die().
#
# WHAT MAKES THIS FAST (see docs/src/cluster_upgrade.md "Optimizations"):
#   1. Packages are pre-downloaded on every node first (predownload.sh) so the
#      per-node `dnf upgrade` installs from cache (~10 min/node saved).
#   2. longhorn_clear_node DELETES replicas that have a healthy copy elsewhere
#      (instant) instead of evicting/rebuilding them (~12-25 min/node saved).
#      Longhorn re-replenishes the deleted replicas in the background after.
#   3. Health gate is a SAFETY FLOOR only: blocks iff a volume is FAULTED
#      (0 replicas). Degraded/reduced-redundancy is allowed during the window.
#
# PREREQUISITES (operator, before running — see README.md):
#   - export SECRET_DOMAIN=<your cluster domain>   (required)
#   - export KVER=<target patch, e.g. 1.36.4>      (default 1.36.4)
#   - Off-cluster etcd snapshot taken.
#   - k8s + cri-o <minor> repos staged on all CentOS nodes; deb repo reachable
#     on containerd nodes. Packages pre-downloaded (predownload.sh).
#   - `kubeadm upgrade apply v$KVER --yes --ignore-preflight-errors=CoreDNSUnsupportedPlugins,CoreDNSMigration --skip-phases=addon`
#     ALREADY RUN on the first control plane (this script does `upgrade node` only).
#   - descheduler suspended (avoids rebalance churn fighting drains).
#   - Longhorn (LIVE, restore after — Phase 5):
#       node-drain-policy = allow-if-replica-is-stopped
#       replica-replenishment-wait-interval = 30
#   - Passwordless root SSH to every node; run from a host that can reach them.
#   - EDIT the node list + order at the bottom for your topology.
#
# GOTCHAS THIS ENCODES (learned the hard way — see runbook "Failure modes"):
#   - Longhorn dual instance-managers (data plane not engine-upgraded) leave a
#     PDB@disruptionsAllowed=0 that blocks `kubectl drain`. drain_node deletes
#     the node's empty IM pods first (kubectl delete bypasses the PDB).
#   - A drain's OSD-delete fallback + an aborted run leaves the node cordoned
#     with its OSD Pending -> ceph stuck N-1/N. Uncordon to let it reschedule.
#   - A replica rebuilding from a source on an evicted node stalls (progress
#     frozen, 0 MBps). Delete the stuck replica so it re-rebuilds from a healthy
#     one. longhorn_clear_node avoids this by deleting, not rebuilding.
# ============================================================================
set -uo pipefail
: "${SECRET_DOMAIN:?export SECRET_DOMAIN=your.cluster.domain}"
D="$SECRET_DOMAIN"
KVER="${KVER:-1.36.4}"
VER="v$KVER"
KHOST="${KHOST:-master1}"   # healthy master used as kubectl proxy (never the target)
ETCDHOST="${ETCDHOST:-master1}"

kc(){ local a q=""; for a in "$@"; do q+=" $(printf '%q' "$a")"; done; ssh -o ConnectTimeout=8 -o BatchMode=yes root@$KHOST.$D "kubectl$q"; }
nssh(){ local h=$1; shift; ssh -o ConnectTimeout=8 -o BatchMode=yes root@$h.$D "$@"; }
die(){ echo "!!!!! ABORT: $* !!!!!"; exit 1; }
ts(){ date '+%H:%M:%S'; }

verify_quorum(){ # require 3/3 etcd members healthy (adjust count for your CP size)
  local out h
  out=$(nssh $ETCDHOST "kubectl -n kube-system exec etcd-$ETCDHOST.$D -- etcdctl --endpoints=https://127.0.0.1:2379 --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key endpoint health --cluster" 2>&1)
  h=$(echo "$out" | grep -c "is healthy")
  echo "  [$(ts)] etcd quorum: $h/3 healthy"
  [ "$h" -eq 3 ] || die "etcd quorum $h/3 (need 3): $out"
}

ceph_gate(){ # wait: all OSD pods Running, no PG/OSD health warns (pre-existing AUTH_INSECURE tolerated)
  local i h det bad tot run
  for i in $(seq 1 180); do
    h=$(kc -n rook-ceph get cephcluster rook-ceph -o jsonpath='{.status.ceph.health}' 2>/dev/null)
    [ "$h" = "HEALTH_ERR" ] && die "ceph HEALTH_ERR"
    det=$(kc -n rook-ceph get cephcluster rook-ceph -o jsonpath='{.status.ceph.details}' 2>/dev/null)
    bad=$(echo "$det" | grep -oE '[A-Z_0-9]+:map\[' | sed 's/:map\[//' | grep -v '^AUTH_INSECURE' | tr '\n' ',')
    tot=$(kc -n rook-ceph get pod -l app=rook-ceph-osd --no-headers 2>/dev/null | grep -c .)
    run=$(kc -n rook-ceph get pod -l app=rook-ceph-osd --no-headers 2>/dev/null | awk '$3=="Running"{c++} END{print c+0}')
    if [ -n "$tot" ] && [ "$tot" != "0" ] && [ "$run" = "$tot" ] && [ -z "$bad" ]; then
      echo "  [$(ts)] ceph gate OK (osd $run/$tot, health=$h)"; return 0
    fi
    [ $((i%6)) -eq 0 ] && echo "  [$(ts)] ceph not clean: osd=$run/$tot health=$h bad=[$bad]"
    sleep 10
  done
  die "ceph gate timeout"
}

longhorn_restore(){ kc -n longhorn-system patch nodes.longhorn.io $1.$D --type=merge \
  -p '{"spec":{"allowScheduling":true,"evictionRequested":false}}' >/dev/null 2>&1; echo "  [$(ts)] longhorn restored on $1"; }

longhorn_clear_node(){ # FAST clear: DELETE each running replica that has a healthy copy on another node
  # (instant, no rebuild wait). Only a replica that is its volume's LAST healthy copy is evicted
  # (rebuild-first). REQUIRES operator OK for temporary reduced redundancy (Longhorn re-replenishes after).
  local n=$1.$D r rname vol he i c
  for r in $(kc get replicas.longhorn.io -n longhorn-system -o jsonpath="{range .items[?(@.spec.nodeID=='$n')]}{.metadata.name}|{.spec.volumeName}|{.status.currentState}{'\n'}{end}" 2>/dev/null | awk -F'|' '$3=="running"{print $1"|"$2}'); do
    rname="${r%%|*}"; vol="${r##*|}"
    he=$(kc get replicas.longhorn.io -n longhorn-system -o jsonpath="{range .items[?(@.spec.volumeName=='$vol')]}{.spec.nodeID}|{.status.currentState}|{.spec.healthyAt}{'\n'}{end}" 2>/dev/null | awk -F'|' -v n="$n" '$1!=n && $2=="running" && $3!=""{c++} END{print c+0}')
    if [ "$he" -ge 1 ]; then
      kc delete replicas.longhorn.io -n longhorn-system "$rname" >/dev/null 2>&1
    else
      echo "  [$(ts)] $vol: last healthy copy is on $1 -> evict/rebuild (not delete)"
    fi
  done
  c=$(kc get replicas.longhorn.io -n longhorn-system -o jsonpath="{range .items[?(@.spec.nodeID=='$n')]}{.status.currentState}{'\n'}{end}" 2>/dev/null | grep -c '^running$')
  kc -n longhorn-system patch nodes.longhorn.io "$n" --type=merge -p "{\"spec\":{\"allowScheduling\":false$([ "$c" -gt 0 ] && echo ',"evictionRequested":true')}}" >/dev/null 2>&1
  if [ "$c" -gt 0 ]; then
    echo "  [$(ts)] $1: $c last-copy replica(s) evicting (rebuild-first)"
    for i in $(seq 1 180); do
      c=$(kc get replicas.longhorn.io -n longhorn-system -o jsonpath="{range .items[?(@.spec.nodeID=='$n')]}{.status.currentState}{'\n'}{end}" 2>/dev/null | grep -c '^running$')
      [ "$c" = "0" ] && break; [ $((i%6)) -eq 0 ] && echo "  [$(ts)] $1: $c last-copy replica(s) left"; sleep 10
    done
  fi
  c=$(kc get replicas.longhorn.io -n longhorn-system -o jsonpath="{range .items[?(@.spec.nodeID=='$n')]}{.status.currentState}{'\n'}{end}" 2>/dev/null | grep -c '^running$')
  [ "$c" = "0" ] || die "$1 still has $c running replicas after clear"
  echo "  [$(ts)] longhorn cleared on $1 (0 running replicas)"
}

longhorn_wait_healthy(){ # SAFETY FLOOR: block iff an ATTACHED volume is FAULTED (0 replicas). Degraded OK.
  local i flt
  for i in $(seq 1 120); do
    flt=$(kc get volumes.longhorn.io -n longhorn-system -o jsonpath="{range .items[*]}{.status.state}{'='}{.status.robustness}{'\n'}{end}" 2>/dev/null | awk -F= '$1=="attached" && $2=="faulted"{c++} END{print c+0}')
    [ "$flt" = "0" ] && { echo "  [$(ts)] longhorn: no faulted attached volumes"; return 0; }
    [ $((i%3)) -eq 0 ] && echo "  [$(ts)] longhorn: $flt FAULTED attached volume(s) - waiting for >=1 replica"
    sleep 10
  done
  die "longhorn: attached volume(s) FAULTED (0 replicas)"
}

already_done(){ [ "$(kc get node $1.$D -o jsonpath='{.status.nodeInfo.kubeletVersion}' 2>/dev/null)" = "$VER" ]; }

cnpg_failover(){ # $1 node (cordon first). Delete primaries -> CNPG promotes a replica elsewhere.
  local w=$1 cl reps primpod i n
  for cl in $(kc get pod -n databases -l cnpg.io/instanceRole=primary --field-selector spec.nodeName=$w.$D \
              -o jsonpath="{range .items[*]}{.metadata.labels.cnpg\.io/cluster}{'\n'}{end}" 2>/dev/null); do
    reps=$(kc get pod -n databases -l "cnpg.io/cluster=$cl,cnpg.io/instanceRole=replica" --field-selector status.phase=Running -o name 2>/dev/null | grep -c .)
    [ "$reps" -ge 1 ] || die "CNPG $cl has 0 running replicas; refuse failover"
    primpod=$(kc get pod -n databases -l "cnpg.io/cluster=$cl,cnpg.io/instanceRole=primary" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    echo "  [$(ts)] cnpg failover $cl: delete primary $primpod ($reps replica ok)"
    kc delete pod -n databases "$primpod" --wait=false >/dev/null 2>&1
  done
  for i in $(seq 1 50); do
    n=$(kc get pod -n databases -l cnpg.io/instanceRole=primary --field-selector spec.nodeName=$w.$D --no-headers 2>/dev/null | grep -c .)
    [ "$n" -eq 0 ] && { echo "  [$(ts)] cnpg: 0 primaries on $w"; return 0; }
    sleep 6
  done
  die "cnpg primaries still on $w after failover"
}

drain_node(){ # pre-delete empty IM pods (their PDB blocks drain) + OSD-host-PDB fallback
  local w=$1 osd rr im
  rr=$(kc get replicas.longhorn.io -n longhorn-system -o jsonpath="{range .items[?(@.spec.nodeID=='$w.$D')]}{.status.currentState}{'\n'}{end}" 2>/dev/null | grep -c '^running$')
  if [ "$rr" = "0" ]; then
    for im in $(kc -n longhorn-system get pods --field-selector spec.nodeName=$w.$D -o name 2>/dev/null | grep instance-manager); do
      echo "  [$(ts)] pre-drain: deleting empty IM $im"; kc delete -n longhorn-system "$im" --grace-period=30 >/dev/null 2>&1
    done
  else
    echo "  [$(ts)] WARN: $w has $rr running replicas at drain time (expected 0)"
  fi
  kc drain $w.$D --ignore-daemonsets --delete-emptydir-data --timeout=300s >/tmp/drain-$w.log 2>&1 &
  local dp=$!
  sleep 75
  if kill -0 $dp 2>/dev/null; then
    osd=$(kc -n rook-ceph get pod -l app=rook-ceph-osd --field-selector spec.nodeName=$w.$D -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    [ -n "$osd" ] && { echo "  [$(ts)] drain stalled; deleting OSD $osd"; kc delete pod -n rook-ceph "$osd" --grace-period=30 >/dev/null 2>&1; }
  fi
  wait $dp 2>/dev/null || die "$w drain failed (see /tmp/drain-$w.log)"
  echo "  [$(ts)] $w drained"
}

pkg_upgrade_reboot(){ # CentOS/cri-o RPM node. $2 optional dnf exclude (e.g. GPU driver hold)
  local w=$1 excl="${2:-}"
  nssh $w "set -e
    dnf install -y kubeadm-$KVER kubelet-$KVER kubectl-$KVER
    kubeadm upgrade node --ignore-preflight-errors=CoreDNSUnsupportedPlugins,CoreDNSMigration
    if [ -n \"$excl\" ]; then ( set -o noglob; dnf upgrade -y $excl ); else dnf upgrade -y; fi  # portable noglob (nodes run zsh; bash 'set -f' != noglob in zsh)
    LK=\$(ls -1 /boot/vmlinuz-* | grep -v rescue | sort -V | tail -1 | sed 's|/boot/vmlinuz-||')
    [ -f /boot/initramfs-\${LK}.img ] || dracut -f /boot/initramfs-\${LK}.img \$LK
    systemctl enable crio kubelet
    systemd-run --on-active=5s --unit=upgrade-reboot systemctl reboot
  " >/tmp/up-$w.log 2>&1 || die "$w package upgrade failed (see /tmp/up-$w.log)"
  echo "  [$(ts)] $w upgraded + reboot triggered"
}

wait_ready(){ local w=$1 i r v; sleep 45
  for i in $(seq 1 80); do
    r=$(kc get node $w.$D --no-headers 2>/dev/null | awk '{print $2}')
    v=$(kc get node $w.$D -o jsonpath='{.status.nodeInfo.kubeletVersion}' 2>/dev/null)
    { [ "${r%%,*}" = "Ready" ] && [ "$v" = "$VER" ]; } && { echo "  [$(ts)] $w Ready@$v"; return 0; }
    [ $((i%4)) -eq 0 ] && echo "  [$(ts)] $w waiting: ready=$r ver=$v"; sleep 15
  done
  die "$w not Ready@$VER after reboot"
}

upgrade_master_light(){ # control-plane node with no OSD/mon/CNPG/Longhorn workload
  local m=$1; echo ">>> [$(ts)] MASTER $m"
  already_done $m && { echo "  [$(ts)] $m already $VER, ensuring uncordoned"; kc uncordon $m.$D >/dev/null 2>&1; return 0; }
  verify_quorum; kc cordon $m.$D >/dev/null 2>&1; drain_node $m
  pkg_upgrade_reboot $m; wait_ready $m; kc uncordon $m.$D >/dev/null 2>&1; verify_quorum
  echo ">>> [$(ts)] MASTER $m DONE"
}

upgrade_worker(){ # $1 short, $2 optional dnf exclude
  local w=$1 excl="${2:-}"; echo ">>> [$(ts)] WORKER $w"
  already_done $w && { echo "  [$(ts)] $w already $VER, ensuring uncordoned"; kc uncordon $w.$D >/dev/null 2>&1; longhorn_restore $w; return 0; }
  verify_quorum; ceph_gate "$w"; longhorn_wait_healthy
  kc cordon $w.$D >/dev/null 2>&1; cnpg_failover $w; longhorn_clear_node $w; drain_node $w
  pkg_upgrade_reboot $w "$excl"; wait_ready $w
  kc uncordon $w.$D >/dev/null 2>&1; longhorn_restore $w; ceph_gate "$w"
  echo ">>> [$(ts)] WORKER $w DONE"
}

# NOTE: containerd/apt GPU node (e.g. DGX) is upgraded k8s-only with the driver+
# kernel HELD (bricking risk on some appliances — update the driver via the
# vendor's own tool separately). Adapt the held-package list to your node.

##### RUN — edit node list/order for your topology #####
echo "########## ROLLING UPGRADE START $(ts) — target $VER ##########"
verify_quorum
# Control planes first (the FIRST one must already have had `kubeadm upgrade apply`):
upgrade_master_light master3
# Workers (hardware-pinned / GPU last; pass a dnf --exclude to hold a pinned GPU driver):
upgrade_worker worker7
upgrade_worker worker3
upgrade_worker worker2
upgrade_worker worker6
upgrade_worker worker5
upgrade_worker worker4
upgrade_worker worker8 "--exclude=nvidia*,cuda*,libnvidia*,kmod-nvidia*"
# GPU/containerd node (spark) handled separately — see README.
# Heaviest control plane LAST (its `apply` was done up front; here only the node bits):
KHOST=master2; ETCDHOST=master2
upgrade_master_light master1   # or a heavy variant with cnpg_failover/longhorn_clear_node if it hosts workload

echo "########## FINAL VERIFY $(ts) ##########"
KHOST=master1; ETCDHOST=master1
kc get nodes -o custom-columns=NAME:.metadata.name,STATUS:.status.conditions[-1].type,VERSION:.status.nodeInfo.kubeletVersion --no-headers 2>/dev/null
ceph_gate none; longhorn_wait_healthy; verify_quorum
echo "########## ROLLING UPGRADE COMPLETE $(ts) — restore Longhorn settings + resume descheduler (Phase 5) ##########"
