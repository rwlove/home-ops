#!/bin/bash

. .cluster-secrets.env

scp root@master1:~/.kube/config ~/.kube/config && \
./init/approve-csrs.sh

echo "Create cert-manager issuer secrets"
envsubst < "./tmpl/cert-manager-secrets.yaml" \
         > "./kubernetes/main/apps/cert-manager/issuers/secrets.yaml"

echo "Encrypt cert-manager issuer secrets"
sops --encrypt --in-place "./kubernetes/main/apps/cert-manager/issuers/secrets.yaml"

## TODO: Where does this get done in the new flow? Remove if working.
#echo "######"
#echo "# Create namespace"
#kubectl create namespace flux-system --dry-run=client -o yaml | kubectl apply -f -

#echo "######"
echo "# Create sops-age Secret"
cat ~/.config/sops/age/keys.txt |
    kubectl -n flux-system create secret generic sops-age \
    --from-file=age.agekey=/dev/stdin

#echo "######"
#echo "# 1st Application"
#kubectl apply --kustomize=./kubernetes/main/base/flux-system

#echo "######"
#echo "# 2nd Application"
#kubectl apply --kustomize=./kubernetes/main/base/flux-system

#echo "######"
echo "# Create Secrets Patch and Push"
stg new -m update-secrets
./create-secrets.sh
stg refresh --no-verify ; stg pop -a ; git pull ; stg push -a ; stg commit -a ; stg clean ; git push

echo "Create Cluster Settings Configmap"
kubectl apply -f ./kubernetes/main/flux/vars/cluster-settings.yaml

echo "Create Cluster Secrets"
sops --decrypt ./kubernetes/main/flux/vars/cluster-secrets.yaml | kubectl apply -f -

echo "Apply Helmfile" # TODO: How do I wait until this is all done?
kubectl apply --server-side --filename -- helmfile --quiet --file ./bootstrap/helmfile.yaml apply --skip-diff-on-install --suppress-diff

ready=0
all_nodes_ready() {
    if [ `kubectl get nodes | grep -c "NotReady"` -eq 0 ] ; then
	ready=1
    else
	ready=0
    fi
}

while [ ${ready} -ne 1 ] ; do
    sleep 1
    all_nodes_ready
done

echo "Create Cluster"
kubectl apply --server-side --kustomize ./kubernetes/main/flux/config
