#!/bin/bash

scp root@master1:~/.kube/config ~/.kube/config && \
./init/approve-csrs.sh && \
kubectl kustomize --enable-helm bootstrap/cilium-quick-install | kubectl apply -f -

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

echo "run ./init/initialize-cluster.sh"
