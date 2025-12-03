set positional-arguments := true
#set quiet := true
set shell := ['bash', '-euo', 'pipefail', '-c']

bootstrap_dir := justfile_dir() + '/bootstrap'

[doc('Apply Kubernetes resources')]
resources:
    if ! just template "bootstrap/resources.yaml.j2" | kubectl apply --server-side --field-manager bootstrap --force-conflicts -f -; then \
        just log fatal "Failed to apply resources" "stage" "$0"; \
    fi;
