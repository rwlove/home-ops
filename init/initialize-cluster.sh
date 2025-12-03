#!/bin/bash

set -eu -o pipefail

scp root@master1:~/.kube/config ~/.kube/config

#echo "######"
echo "# Create Secrets Patch and Push"
./create-secrets.sh

ERR=`kubectl get ns flux-system`
if [[ "${ERR}" -eq 1 ]] ; then
    echo "Create flux-system namespace"
    kubectl create namespace flux-system
else
    echo "flux-system namespace already exists"
fi
    
echo "Create Cluster Settings Configmap"
kubectl apply -f ./kubernetes/main/flux/vars/cluster-settings.yaml

echo "Create Cluster Secrets"
sops --decrypt ./kubernetes/main/flux/vars/cluster-secrets.yaml | kubectl apply -f -

echo "Apply CRDS"
helmfile -f "bootstrap/helmfile.d/00-crds.yaml" template -q | kubectl apply --server-side -f

#echo "Apply Helmfile"
#helmfile -f "bootstrap/helmfile.yaml" sync --hide-notes

#echo "Apply Apps"
#helmfile -f "bootstrap/helmfile.d/01-apps.yaml" sync --hide-notes

exit 1

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
