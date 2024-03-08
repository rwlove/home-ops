## :memo:&nbsp; Bootstrap

1. ./init/prepare-cluster.sh
#1. Deploy [cilium](https://cilium.io/) : `kubectl kustomize --enable-helm ./kubernetes/main/bootstrap/cilium-quick-install | kubectl apply -f -`
#2. Approve CSRs created by kubeadm `./init/approve-csrs.sh`
3. Deploy [flux](https://github.com/fluxcd/flux2) `kubectl apply --server-side --kustomize ./kubernetes/main/bootstrap/flux`

## done in initialize-cluster.sh
# 4. Create flux github secret `sops --decrypt ./kubernetes/bootstrap/flux/github-deploy-key.sops.yaml | kubectl apply -f -`
# 5. Create sops secret `cat ~/.config/sops/age/keys.txt | kubectl create secret generic sops-age --namespace=flux-system --from-file=age.agekey=/dev/stdin`
# 6. Apply flux cluster variables `kubectl apply -k ./kubernetes/flux/vars/cluster-settings.yaml`
# 6. Apply flux cluster secrets `sops --decrypt ./kubernetes/flux/vars/cluster-secrets.sops.yaml | kubectl apply -f -`

## I think the helm chart will install this
#7. Apply prometheus CRDs `kubectl apply -f https://raw.githubusercontent.com/prometheus-community/helm-charts/main/charts/kube-prometheus-stack/crds/crd-prometheuses.yaml`

4. Apply flux kustomization `kubectl apply --server-side --kustomize ./kubernetes/main/flux/config`

TODO: Add link to this doc from main README.md, remove old install procedure