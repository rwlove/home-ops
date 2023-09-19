#!/usr/bin/env bash
set -o errexit

KUBERNETES_DIR=$1
SCHEMA_DIR=$2
KUBE_VERSION="${3:-1.26.0}"

[[ -z "${KUBERNETES_DIR}" ]] && echo "Kubernetes location not specified" && exit 1
[[ -z "${SCHEMA_DIR}" ]] && echo "Schema location not specified" && exit 1

kustomize_args=("--load-restrictor=LoadRestrictionsNone")
kustomize_config="kustomization.yaml"
kubeconform_args=(
    "-strict"
    "-ignore-missing-schemas"
    "-kubernetes-version"
    "${KUBE_VERSION}"
    "-skip"
    "Secret"
    "-schema-location"
    "default"
    "-schema-location"
    "${SCHEMA_DIR}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"
    "-verbose"
)

echo "=== Validating kustomizations in ${KUBERNETES_DIR}/crds ==="
find "${KUBERNETES_DIR}/crds" -type f -name $kustomize_config -print0 | while IFS= read -r -d $'\0' file;
  do
    echo "=== Validating kustomizations in ${file/%$kustomize_config} ==="
    kustomize build "${file/%$kustomize_config}" "${kustomize_args[@]}" | \
      kubeconform "${kubeconform_args[@]}"
    if [[ ${PIPESTATUS[0]} != 0 ]]; then
      exit 1
    fi
done

echo "=== Validating kustomizations in ${KUBERNETES_DIR}/base ==="
find "${KUBERNETES_DIR}/base" -type f -name $kustomize_config -print0 | while IFS= read -r -d $'\0' file;
  do
    echo "=== Validating kustomizations in ${file/%$kustomize_config} ==="
    kustomize build "${file/%$kustomize_config}" "${kustomize_args[@]}" | \
      kubeconform "${kubeconform_args[@]}"
    if [[ ${PIPESTATUS[0]} != 0 ]]; then
      exit 1
    fi
done

echo "=== Validating kustomizations in ${KUBERNETES_DIR}/apps ==="
find "${KUBERNETES_DIR}/apps" -type f -name $kustomize_config -print0 | while IFS= read -r -d $'\0' file;
  do
    echo "=== Validating kustomizations in ${file/%$kustomize_config} ==="
    kustomize build "${file/%$kustomize_config}" "${kustomize_args[@]}" | \
      kubeconform "${kubeconform_args[@]}"
    if [[ ${PIPESTATUS[0]} != 0 ]]; then
      exit 1
    fi
done
