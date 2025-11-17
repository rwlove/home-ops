#!/bin/bash

flux --namespace flux-system reconcile kustomization cluster --with-source
