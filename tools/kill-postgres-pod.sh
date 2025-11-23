#!/bin/bash

pod=$1
pvc=$1
pv=`kubectl -n databases describe pvc $pod | grep Volume: | awk '{ print $2 }'`

kubectl delete pv $pv &
kubectl -n databases delete pvc $pvc &
kubectl -n databases delete pods $pod
