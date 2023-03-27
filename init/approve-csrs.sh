#!/bin/bash

kubectl get csr | grep Pending | cut -d " " -f 1 | xargs kubectl certificate approve
