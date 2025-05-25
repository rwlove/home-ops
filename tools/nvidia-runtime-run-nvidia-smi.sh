#!/bin/bash

#kubectl -n default run nvidia-shell --rm -i --tty --overrides='{"apiVersion": "v1", "spec": {"nodeSelector": {"nvidia.com/gpu.present": "true"}, "runtimeClassName": "nvidia"}}' --image nvidia/cuda:12.6.2-devel-ubuntu22.04 -- nvidia-smi

kubectl -n default run nvidia-shell -i --tty --overrides='{"apiVersion": "v1", "spec": {"nodeSelector": {"nvidia.com/gpu.present": "true"}, "runtimeClassName": "nvidia"}}' --image nvidia/cuda:12.6.2-devel-ubuntu22.04 -- nvidia-smi
