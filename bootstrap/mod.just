set positional-arguments := true
#set quiet := true
set shell := ['bash', '-euo', 'pipefail', '-c']

bootstrap_dir := justfile_dir() + '/bootstrap'

[doc('Apply Kubernetes resources')]
resources:
    if ! just template "bootstrap/resources.yaml.j2" | kubectl apply --server-side --field-manager bootstrap --force-conflicts -f -; then \
        just log fatal "Failed to apply resources" "stage" "$0"; \
    fi;

# Module recipes run with cwd = bootstrap/, so helmfile paths are relative to
# this dir. The CRDs must land before any CRD-typed manifest is validated.
[doc('Extract and apply bootstrap CRDs (helmfile.d/00-crds.yaml)')]
crds:
    helmfile -f "helmfile.d/00-crds.yaml" template -q | kubectl apply --server-side --field-manager bootstrap --force-conflicts -f -

[doc('Install the pre-Flux bootstrap apps (helmfile.d/01-apps.yaml)')]
apps:
    helmfile -f "helmfile.d/01-apps.yaml" sync --hide-notes
