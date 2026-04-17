# Correct schemas

Whenever requested to fix or correct schemas, follow these instructions:

** Default rules **
- only apply schemas to yaml files
- do not apply schemas to files in any directory named `resources`
- if you cannot find the right schema to apply to a file then apend "# TODO: apply schema"
- schemas should always follow `---`
- there is always a schema defined on the second line of a yaml file
- remove incorrect schemas and replace them with the correct schema comment

## apiVersion to schema mappings ##
`kustomize.toolkit.fluxcd.io/v1` -> `# yaml-language-server: $schema=https://raw.githubusercontent.com/fluxcd-community/flux2-schemas/main/kustomization-kustomize-v1.json`
`helm.toolkit.fluxcd.io/v2` -> `# yaml-language-server: $schema=https://raw.githubusercontent.com/fluxcd-community/flux2-schemas/main/helmrelease-helm-v2.json`
`source.toolkit.fluxcd.io/v1` -> `# yaml-language-server: $schema=https://raw.githubusercontent.com/fluxcd-community/flux2-schemas/refs/heads/main/ocirepository-source-v1.json`
`kustomize.config.k8s.io/v1beta1` -> `# yaml-language-server: $schema=https://json.schemastore.org/kustomization`
`v1` and `kind: PersistentVolumeClaim` -> `# yaml-language-server: $schema=https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/v1.26.1-standalone-strict/persistentvolumeclaim-v1.json`
`v1` and `kind: PersistentVolume` -> `# yaml-language-server: $schema=https://raw.githubusercontent.com/yannh/kubernetes-json-schema/refs/heads/master/master/persistentvolume-v1.json`
`external-secrets.io/v1` -> `# yaml-language-server: $schema=https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/external-secrets.io/externalsecret_v1.json`
