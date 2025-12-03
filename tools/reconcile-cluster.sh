#!/bin/bash

flux --namespace flux-system reconcile kustomization cluster-apps --with-source
