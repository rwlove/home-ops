#!/bin/bash

for node in worker1 worker2 worker3 worker4 worker5 worker6 worker7 worker8 worker9 master1 master2 master3 ; do
    ssh root@$node $@
done
