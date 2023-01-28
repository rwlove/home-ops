#!/bin/bash

kubectl get deployments -A | awk '$3!=$4 || $4!=$5 {print $0}' | grep "/0"

kubectl get replicasets -A | awk '$3!=$4 || $4!=$5 {print $0}' | grep "/0"

kubectl get statefulsets -A | awk '$3!=$4 || $4!=$5 {print $0}' | grep "/0"
