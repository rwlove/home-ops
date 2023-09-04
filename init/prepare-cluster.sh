#!/bin/bash -x

#scp root@master1:~/.kube/config ~/.kube/config && \
#./init/approve-csrs.sh && \
#kubectl kustomize --enable-helm bootstrap/cilium-quick-install | kubectl apply -f -

all_nodes_ready() {
    if [ `kubectl get nodes | grep -c "NotReady"` -eq 0 ] ; then
	echo 1
    else
	echo 0
    fi
}

while [ all_nodes_ready == 0 ] ; do
    sleep 1
done
