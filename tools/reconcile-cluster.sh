#!/bin/bash

flux --namespace flux-system reconcile kustomization flux-instance --with-source
