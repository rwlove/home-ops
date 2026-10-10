set quiet := true
set shell := ['bash', '-euo', 'pipefail', '-c']

# Laptop-side cluster lifecycle entry points. Thin wrappers over the init/
# scripts so operators get one discoverable surface (`just cluster …`)
# alongside `just bootstrap …` and `just k8s …`.
#
# Module recipes run with cwd = init/, but the scripts expect the repo root
# (they reference ./kubernetes/… and bootstrap/…), so each recipe cd's up.
#
# create-cluster.sh and kube-vip.sh are intentionally absent: they run ON
# master1, not the laptop. (A separate change may make them laptop-driven;
# until then invoke them on the control-plane host directly.)

[doc('Bootstrap in-cluster apps from the laptop (kubeconfig → secrets → CRDs → apps → Flux)')]
init:
    cd .. && ./init/initialize-cluster.sh

[doc('DESTRUCTIVE teardown — drains nodes, kubeadm reset, wipes Ceph/etcd (prompts to confirm)')]
destroy:
    cd .. && ./init/destroy-cluster.sh

[doc('Approve pending node CSRs (fallback when kubelet-csr-approver is not up yet)')]
approve-csrs:
    cd .. && ./init/approve-csrs.sh
