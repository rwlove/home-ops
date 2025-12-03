## Prereqe
* 1password-cli
* minijinja
* yq
* go-task (alias to task)

## Initialization
```./init/create-cluster.sh``` (on master)

```./init/initialize-cluster.sh``` (on laptop)

```ssh root@master1 rm /etc/kubernetes/manifests/kube-vip.yaml``` (on laptop)

## Teardown
```./init/destroy-cluster.sh``` (on laptop)
