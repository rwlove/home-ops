#!/bin/bash

for node in worker2 worker3 worker4 worker5 worker6 worker7 worker8 worker9 master1 master2 master3 ; do
    echo "## node ${node}"
    ssh root@$node $@
done
