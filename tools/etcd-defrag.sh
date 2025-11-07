#!/bin/bash

kubectl exec $(kubectl get pods --selector=component=etcd -A -o name | head -n 1) -n kube-system -- etcdctl defrag --cluster \
--cacert /etc/kubernetes/pki/etcd/ca.crt \
--key /etc/kubernetes/pki/etcd/server.key \
--cert /etc/kubernetes/pki/etcd/server.crt   
