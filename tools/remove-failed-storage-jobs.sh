#!/bin/bash

for job in `kubectl -n storage get jobs | grep Failed | cut -d " " -f 1` ; do
    kubectl -n storage delete jobs $job
done
