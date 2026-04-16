# Sorting instructions for all fluxcd toolkit kustomize yaml files

Whenever asked to sort these files, follow these instructions:

- **Default rule**: All fields and properties should be sorted alphabetically at every level of the YAML structure, regardless of how deeply nested they are, unless a specific override rule is provided below or in other applicable instructions files.

## Override rules for Kubernetes related file types

- There should be no `namespace` field, remove it if it exists

- Whenever they are present on the same level of a YAML structure, these fields should be sorted as follows:
  - "---" (required, insert at top if missing)
  - "# yaml-language-server: $schema=" (if missing, add "# TODO: add schema")
  - `apiVersion`
  - `kind`
  - `metadata`
  - `spec`

- The items within the `spec` section should be sorted as follows:
  - `commonMetadata`
  - `targetNamespace`
  - `path`
  - `interval`
  - `timeout`
  - `prune`
  - `wait`
  - `sourceRef`
  - `dependsOn`
  - `postBuild`

- The items within the `sourceRef` section should be sorted as follows:
  - `kind`
  - `name`
  - `namespace`

- The items within the `dependsOn` section are formatted as a list and each list entry should be sorted as follows:
  - `name`
  - `namespace`
