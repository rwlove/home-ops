#!/usr/bin/env bash
# ============================================================================
# Parallel package pre-download for a rolling k8s upgrade.
# ============================================================================
# Warms every node's package cache BEFORE the upgrade so each node's real
# `dnf upgrade` / `apt upgrade` installs from disk (~10 min/node of download
# removed from the critical path). Pure cache-warming — installs nothing,
# reboots nothing, zero cluster impact. Run it, let it finish, THEN run
# rolling-upgrade.sh.
#
#   export SECRET_DOMAIN=your.cluster.domain
#   export KVER=1.36.4
#   bash predownload.sh
#
# Edit CENTOS / the containerd node list for your topology. Skip any node that
# is currently mid-upgrade (a second dnf/apt would clash on the lock).
# ============================================================================
set -uo pipefail
: "${SECRET_DOMAIN:?export SECRET_DOMAIN=your.cluster.domain}"
D="$SECRET_DOMAIN"
KVER="${KVER:-1.36.4}"
CENTOS="${CENTOS:-worker2 worker3 worker4 worker5 worker6 worker7 worker8 master1 master2 master3}"
CONTAINERD="${CONTAINERD:-spark}"   # apt/containerd nodes (GPU appliance etc.); empty to skip

for n in $CENTOS; do
  # worker8 example: hold the GPU driver out of the cached upgrade set
  excl=""; [ "$n" = worker8 ] && excl="--exclude=nvidia*,cuda*,libnvidia*,kmod-nvidia*"
  (
    ssh -o ConnectTimeout=10 -o BatchMode=yes root@$n.$D "set -f
      dnf install -y --downloadonly --setopt=keepcache=1 kubeadm-$KVER kubelet-$KVER kubectl-$KVER >/tmp/predl-k8s.log 2>&1
      dnf upgrade -y --downloadonly --setopt=keepcache=1 $excl >/tmp/predl-full.log 2>&1
      echo cached
    " 2>&1 | tail -1 | sed "s/^/$n: /"
  ) &
done
for n in $CONTAINERD; do
  (
    ssh -o ConnectTimeout=10 -o BatchMode=yes root@$n.$D "
      f=\$(grep -rl pkgs.k8s.io /etc/apt/sources.list.d/ 2>/dev/null | head -1)
      [ -n \"\$f\" ] && sed -i 's#v1\.[0-9][0-9]#v${KVER%.*}#g' \"\$f\"   # point deb repo at target minor
      apt-get update -qq >/dev/null 2>&1
      apt-get -y --download-only upgrade >/tmp/predl-full.log 2>&1
      echo cached
    " 2>&1 | tail -1 | sed "s/^/$n: /"
  ) &
done
wait
echo "===== ALL PRE-DOWNLOADS COMPLETE ====="
