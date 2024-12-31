#!/bin/bash

#kubectl get pods -A -o jsonpath='{.items[?(@.spec.runtimeClassName=="nvidia")].metadata.name}' | tr ' ' '\n'

for pod in `kubectl get pods -A -o jsonpath='{.items[?(@.spec.runtimeClassName=="nvidia")].metadata.name}'` ; do
    kubectl get pods -A -o wide | grep $pod
done

echo
