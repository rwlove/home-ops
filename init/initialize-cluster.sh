#!/bin/bash

scp root@master1:~/.kube/config ~/.kube/config

#echo "######"
echo "# Create Secrets Patch and Push"
./create-secrets.sh

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
