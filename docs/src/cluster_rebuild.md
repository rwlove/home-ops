# Cluster Rebuild Actions

## Before Cluster Rebuild
1. Restore CNPG from backup

uncomment section in cluster.yaml

## After Cluster Rebuild

1. Update KUBECONFIG secret in github/home-ops

Settings -> (left side) Secrets and Variables -> (submenu) Actions

Edit KUBECONFIG secret with `cat ~/.kube/config | base64`




