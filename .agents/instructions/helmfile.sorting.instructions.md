# Sorting instructions for all yaml files

Whenever asked to sort these files, follow these instructions:

- **Default rule**: All fields and properties should be sorted alphabetically at every level of the YAML structure, regardless of how deeply nested they are, unless a specific override rule is provided below or in other applicable instructions files.

## Override rules for Kubernetes related file types

- Whenever they are present on the same level of a YAML structure, these fields should be sorted as follows:
  - `apiVersion`
  - `kind`
  - `metadata`
  - `spec`

- The items within the `metadata` section should be sorted as follows:
  - `name`
  - `namespace`
  - `annotations`
  - `labels`

## HelmReleases based on app-template

This section gives instructions specifically for HelmReleases that are based on the `app-template` chart. Identify them by `spec.chartRef.name == app-template`.

Do **not** identify them by a sidecar `ocirepository.yaml` referencing
`oci://ghcr.io/bjw-s-labs/helm/app-template` — that rule was wrong for
109 of the 110 app-template HelmReleases in this repo. The chart is
delivered through one shared `OCIRepository` supplied by the
`kubernetes/components/repos/app-template` kustomize Component, so only
`onepassword-connect` has a sidecar of its own. An agent following the
old rule concludes the repo has a single app-template HelmRelease.

### Sorting rules

Whenever asked to sort these files, follow these instructions:

- Whenever there is an `enabled` field, it should be the first field within its section.

- The items within the `spec` section should be sorted as follows:
  - `chartRef`
  - `interval`
  - `dependsOn`
  - `install`
  - `upgrade`
  - `values`

- Items within the `spec.values` sections should be sorted as follows:
  - `defaultPodOptions`
  - `controllers`
  - `service`
  - `route`
  - `persistence`
  - Any other fields should be added next in alphabetical order.

### Detailed sorting rules for nested sections

- Items within the `spec.values.controllers.*` sections should be sorted as follows:
  - `pod`
  - Any other fields should be added next in alphabetical order.
  - `initContainers`
  - `containers`

- Items within `spec.values.controllers.*.containers.*` sections should be sorted as follows:
  - `image`
  - Any other fields should be added next in alphabetical order.

- Items within `spec.values.controllers.*.containers.resources` and `spec.values.controllers.*.initContainers.resources` sections should be sorted as follows:
  - `requests`
  - `limits`

- Items within `spec.values.service.*` sections should be sorted as follows:
  - `type`
  - Any other fields should be added next in alphabetical order.

- Items within `persistence.*` sections should be sorted as follows:
  - `type`
  - Any other fields should be added next in alphabetical order.
  - `globalMounts`
  - `advancedMounts`
