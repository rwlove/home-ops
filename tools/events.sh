#!/bin/bash

kubectl get events -A --watch-only | grep -vE 'ArtifactUpToDate|ReconciliationSucceeded|GitOperationSucceeded|DependencyNotReady|ImageGCFailed|CacheOperationFailed'
