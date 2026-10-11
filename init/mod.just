set quiet := true
set shell := ['bash', '-euo', 'pipefail', '-c']

# Laptop-side cluster lifecycle entry points. Thin wrappers over the init/
# scripts so operators get one discoverable surface (`just cluster …`)
# alongside `just bootstrap …` and `just k8s …`.
#
# Module recipes run with cwd = init/, but the scripts expect the repo root
# (they reference ./kubernetes/… and bootstrap/…), so each recipe cd's up.

[doc('End-to-end cluster bring-up from the laptop (kube-vip → kubeadm → join → bootstrap apps)')]
create:
    cd .. && ./init/create-cluster.sh

[doc('DESTRUCTIVE teardown — drains nodes, kubeadm reset, wipes Ceph/etcd (prompts to confirm)')]
destroy:
    cd .. && ./init/destroy-cluster.sh

[doc('Approve pending node CSRs (fallback when kubelet-csr-approver is not up yet)')]
approve-csrs:
    cd .. && ./init/approve-csrs.sh
