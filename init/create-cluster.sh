#!/bin/bash
#
# Laptop-driven, end-to-end cluster bring-up. Run from the repo root on the
# LAPTOP (the repo no longer needs to exist on master1). Orchestrates master1
# over ssh: kube-vip → kubeadm init → join masters/workers → Longhorn labels →
# in-cluster bootstrap apps. Equivalent to the old create-cluster.sh (on
# master1) + initialize-cluster.sh (on laptop), collapsed into one flow.
#
# Prefer `just cluster create`. Requires: SECRET_DOMAIN, root ssh to every
# node, and the laptop toolchain (kubectl, just, helmfile, helm, op,
# minijinja-cli, yq, envsubst).
set -eu -o pipefail

: "${SECRET_DOMAIN:?SECRET_DOMAIN must be set (export SECRET_DOMAIN=<your-cluster-domain>)}"

# Canonical node lists (MASTER_INIT, JOIN_MASTERS, WORKERS, LONGHORN_NODES).
source "$(dirname "${BASH_SOURCE[0]}")/nodes.sh"

# kube-vip static-pod params — keep in sync with init/kube-vip.sh, which is the
# standalone generator used by promote_worker_to_control_plane.md.
VIP="192.168.6.1"
VIP_INTERFACE="enp0s31f6"
KUBE_VIP_VERSION="v1.0.2"

m1="root@${MASTER_INIT}.${SECRET_DOMAIN}"

# Load br_netfilter + enable IPv4 forwarding on a node (short hostname).
prep_netfilter() {
    ssh "root@${1}.${SECRET_DOMAIN}" "modprobe br_netfilter; echo '1' > /proc/sys/net/ipv4/ip_forward"
}

echo "#### Reset local kubeconfig ####"
rm -rf "${HOME}/.kube"/* 2>/dev/null || true
mkdir -p "${HOME}/.kube"

echo "#### [1/6] Render kube-vip static pod on ${MASTER_INIT} ####"
# Rendered BEFORE kubeadm init so the control-plane VIP is reachable for
# controlPlaneEndpoint. kube-vip uses super-admin.conf (post-1.29 RBAC); it
# crashloops until kubeadm init creates that file, then stabilizes.
ssh "$m1" "mkdir -p /etc/kubernetes/manifests && \
  podman run --network host --rm ghcr.io/kube-vip/kube-vip:${KUBE_VIP_VERSION} manifest pod \
    --interface ${VIP_INTERFACE} --vip ${VIP} --vipSubnet 32 --controlplane --arp --leaderElection \
  | sed 's#path: /etc/kubernetes/admin.conf#path: /etc/kubernetes/super-admin.conf#' \
  | tee /etc/kubernetes/manifests/kube-vip.yaml >/dev/null"

echo "#### [2/6] kubeadm init on ${MASTER_INIT} ####"
prep_netfilter "${MASTER_INIT}"
# kubeadm doesn't expand env vars; render clusterconfiguration.yaml locally and
# copy it over.
rendered=$(mktemp -t clusterconfiguration.XXXXXX.yaml)
trap 'rm -f "$rendered"' EXIT
envsubst < ./init/clusterconfiguration.yaml > "$rendered"
scp "$rendered" "${m1}:/tmp/clusterconfiguration.yaml"
ssh "$m1" "kubeadm init --skip-phases=addon/kube-proxy,addon/coredns --config /tmp/clusterconfiguration.yaml"

echo "#### [3/6] Pull kubeconfig to laptop ####"
scp "${m1}:/etc/kubernetes/admin.conf" "${HOME}/.kube/config"

echo "#### [4/6] Join control-plane + worker nodes ####"
certs=$(ssh "$m1" "kubeadm init phase upload-certs --upload-certs --config /tmp/clusterconfiguration.yaml" | tail -n 1)
echo "certs: ${certs}"
worker_join_cmd=$(ssh "$m1" "kubeadm token create --print-join-command")
master_join_cmd="${worker_join_cmd} --control-plane --certificate-key ${certs}"

for cp_host in "${JOIN_MASTERS[@]}" ; do
    echo "########## Joining (master) ${cp_host} #"
    prep_netfilter "$cp_host"
    ssh "root@${cp_host}.${SECRET_DOMAIN}" "mkdir -p /etc/kubernetes/manifests && ${master_join_cmd}"
done

for worker_host in "${WORKERS[@]}" ; do
    echo "########## Joining (worker) ${worker_host} #"
    prep_netfilter "$worker_host"
    ssh "root@${worker_host}.${SECRET_DOMAIN}" "mkdir -p /etc/kubernetes/manifests && ${worker_join_cmd}"
done

echo "#### [5/6] Longhorn disk labels + make ${MASTER_INIT} schedulable ####"
# worker8 intentionally excluded (diskless in Longhorn — see nodes.sh).
for longhorn_host in "${LONGHORN_NODES[@]}" ; do
    node="${longhorn_host}.${SECRET_DOMAIN}"
    ssh "root@${node}" "rm -rf /var/lib/longhorn/*"
    kubectl label nodes "${node}" "node.longhorn.io/create-default-disk=true" --overwrite
done
kubectl taint nodes "${MASTER_INIT}.${SECRET_DOMAIN}" node-role.kubernetes.io/control-plane:NoSchedule- || true

echo "#### [6/6] Bootstrap in-cluster apps ####"
for ns in flux-system observability network cert-manager external-secrets ; do
    kubectl apply -f "./kubernetes/apps/${ns}/namespace.yaml"
done
just bootstrap resources
kubectl -n flux-system apply -f ./kubernetes/flux/meta/cluster-config.yaml
just bootstrap crds
just bootstrap apps

cat <<EOF

#### Done — control plane up and Flux bootstrapping. ####

Once Flux reconciles the in-cluster kube-vip DaemonSet (a few minutes), remove
the temporary static pod so it stops fighting for the VIP:

  ssh ${m1} rm /etc/kubernetes/manifests/kube-vip.yaml

Watch progress:
  flux get ks -A
  flux get hr -A
EOF
