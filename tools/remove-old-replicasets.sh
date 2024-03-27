#!/bin/bash

kubectl get replicaset --all-namespaces -o=jsonpath='{range .items[?(@.spec.replicas==0)]}{.metadata.name}{"\t"}{.metadata.namespace}{"\n"}{end}' | \
    awk '{print $1 " --namespace=" $2}' | \
    xargs -n 2 -d '\n' bash -c 'kubectl delete replicaset $0 $1'
