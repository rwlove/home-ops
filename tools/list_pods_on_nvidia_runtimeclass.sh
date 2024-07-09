#!/bin/bash

kubectl get pods -A -o jsonpath='{.items[?(@.spec.runtimeClassName=="nvidia")].metadata.name}' | tr ' ' '\n'

echo
