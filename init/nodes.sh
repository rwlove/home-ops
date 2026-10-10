#!/bin/bash
# Canonical node inventory for the init/ bootstrap + teardown scripts.
# Source this (don't execute it). Short hostnames only; callers append
# ".${SECRET_DOMAIN}". Keeping the lists here means adding or removing a
# node is a one-file edit instead of hunting through create/destroy.

# The node kubeadm init runs on (control-plane bootstrap host).
MASTER_INIT="master1"

# Additional control-plane nodes joined after init.
JOIN_MASTERS=(master2 master3)

# Worker nodes joined to the cluster.
WORKERS=(worker2 worker3 worker4 worker5 worker6 worker7 worker8)

# Nodes labelled for a default Longhorn disk. worker8 is intentionally
# excluded — it is the P40 GPU node and is kept diskless in Longhorn to
# protect GPU I/O (see .agents/instructions/gpu-routing.md and memory
# reference_worker8_diskless_longhorn_datalocality).
LONGHORN_NODES=(master1 worker2 worker3 worker4 worker5 worker6 worker7)

# Teardown drain/delete order: workers first, then masters in reverse so
# master1 (init node, etcd leader, VIP holder) goes last.
DRAIN_ORDER=("${WORKERS[@]}" master3 master2 master1)

# Nodes that get the Ceph device cleanup during teardown.
CEPH_CLEANUP_NODES=(master1 "${WORKERS[@]}")
