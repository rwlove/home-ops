#!/bin/bash

set -euo pipefail

kubectl get csr | grep Pending | cut -d " " -f 1 | xargs -r kubectl certificate approve
