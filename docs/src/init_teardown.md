## Initialization
```./init/create-cluster.sh``` (on master)

```./init/prepare-cluster.sh``` (on laptop)

```./init/initialize-cluster.sh``` (on laptop)

```ssh root@master1 rm /etc/kubernetes/manifests/kube-vip.yaml``` (on laptop)

## Teardown
```./init/destroy-cluster.sh``` (on laptop)
