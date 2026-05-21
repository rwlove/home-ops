# Debugging

Operator-first reference for triaging issues in this cluster. Roughly ordered from most-used to least.

## First moves

When something is broken, work outside-in: events, then logs, then state.

```sh
# Recent cluster-wide events (the most underrated diagnostic in k8s)
kubectl get events -A --sort-by=.lastTimestamp | tail -50

# Pod state across the cluster — anything not Running/Completed
kubectl get pods -A | grep -v -E 'Running|Completed'

# Flux reconciliation state (anything False is a current failure)
flux get all -A | grep -v True
```

## Pods

```sh
# Describe a pod (events, conditions, container state)
kubectl -n <ns> describe pod <pod>

# Current logs
kubectl -n <ns> logs <pod> -c <container>          # one container
kubectl -n <ns> logs <pod> --all-containers --tail=200

# Previous-incarnation logs (after a crash)
kubectl -n <ns> logs <pod> -c <container> --previous

# Per-pod events only
kubectl -n <ns> get events --field-selector involvedObject.name=<pod>

# Exec into a running container
kubectl -n <ns> exec -it <pod> -c <container> -- sh

# Run an ad-hoc debug pod on the same node
kubectl debug -n <ns> <pod> --image=nicolaka/netshoot --target=<container>
```

`nicolaka/netshoot` ships `dig`, `curl`, `tcpdump`, `iperf3`, `nmap`, `mtr` — keep it as the default network-debug image.

## Flux

```sh
# What's reconciling, what's failed, what's suspended
flux get sources git -A
flux get ks -A
flux get hr -A
flux get all -A | grep -v True

# Force reconciliation
flux reconcile source git flux-system
flux reconcile ks cluster-apps -n flux-system
flux reconcile hr <name> -n <ns>

# Why is this HelmRelease unhappy?
kubectl -n <ns> describe hr <name>
kubectl logs -n flux-system deploy/helm-controller --tail=200 | grep -A5 <name>

# Why is this Kustomization unhappy?
kubectl -n <ns> describe ks <name>
kubectl logs -n flux-system deploy/kustomize-controller --tail=200 | grep -A5 <name>
```

A Kustomization with `Suspended: True` is almost always deliberate — see CLAUDE.md "Flux suspend / disable workflow" and `git log --oneline | grep -E 'disable-|Revert.*disable-'` before touching it.

## Ceph

```sh
# Cluster health from inside the cluster
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph -s
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph osd tree
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph osd status
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- ceph health detail

# OSD pods + per-node mapping
kubectl -n rook-ceph get pods -l app=rook-ceph-osd -o wide

# Watch a recovery in progress
kubectl -n rook-ceph exec -it deploy/rook-ceph-tools -- watch ceph -s

# Per-host PDB state (these block drains during recovery)
kubectl get pdb -n rook-ceph | grep rook-ceph-osd-host
```

Ceph dashboard password is fetched via [`tools/get-ceph-password.sh`](https://github.com/rwlove/home-ops/blob/main/tools/get-ceph-password.sh).

## Longhorn

```sh
# Per-node replica state (load-bearing during drains)
kubectl get replicas.longhorn.io -n longhorn-system -o wide

# Per-node Longhorn node state
kubectl get nodes.longhorn.io -n longhorn-system

# Watch evictions evacuate a node
kubectl patch -n longhorn-system nodes.longhorn.io <node> \
  --type=merge -p '{"spec":{"allowScheduling":false,"evictionRequested":true}}'
# (wait for the node's replica count to reach 0)

# Re-enable after the node is back
kubectl patch -n longhorn-system nodes.longhorn.io <node> \
  --type=merge -p '{"spec":{"allowScheduling":true,"evictionRequested":false}}'

# Volume detail
kubectl -n longhorn-system get volumes.longhorn.io <pvc-uuid>
```

If `longhorn-manager-X` is in CrashLoopBackOff with `bind: address already in use`, an old daemon process is orphaned on the host:

```sh
ssh root@<node> 'pgrep -af "longhorn-manager -d daemon" | xargs -r kill -9'
kubectl -n longhorn-system delete pod longhorn-manager-X
```

## CNPG (Postgres)

```sh
# Cluster status (use the cnpg plugin)
kubectl cnpg -n databases status <cluster-name>

# Quick SQL
kubectl cnpg -n databases psql <cluster-name> -- -c '\l'
kubectl cnpg -n databases psql <cluster-name> -- -c 'SELECT pg_is_in_recovery(), now();'

# Force a primary failover before draining a node
kubectl cnpg promote -n databases <cluster-name> <replica-pod-name>

# Backups
kubectl -n databases get backups.postgresql.cnpg.io
kubectl -n databases get scheduledbackups.postgresql.cnpg.io
```

For recovery, see [`cluster_rebuild.md`](cluster_rebuild.md) → "CNPG recovery from Garage".

## Networking

```sh
# DNS from inside the cluster
kubectl run -it --rm dns --image=nicolaka/netshoot --restart=Never -- \
  dig +short <service>.<ns>.svc.cluster.local

# External DNS resolution from a pod
kubectl run -it --rm dns --image=nicolaka/netshoot --restart=Never -- \
  dig +short google.com

# Reach a service from another pod
kubectl run -it --rm curl --image=curlimages/curl --restart=Never -- \
  curl -v http://<service>.<ns>.svc.cluster.local

# HTTPRoutes + gateway status
kubectl get httproutes -A
kubectl get gateways -A
kubectl describe gateway -n network external

# Cilium quick checks
kubectl -n kube-system exec -it ds/cilium -- cilium status --brief
kubectl -n kube-system exec -it ds/cilium -- cilium bgp peers
kubectl get ciliumloadbalancerippool
```

DNS troubleshooting reference: <https://kubernetes.io/docs/tasks/administer-cluster/dns-debugging-resolution/>.

External resolver sanity check (from the laptop): <https://dnschecker.org>.

## Etcd

```sh
# Quorum + per-member latency (run on any control plane)
kubectl exec -n kube-system etcd-master1.${SECRET_DOMAIN} -- etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  endpoint status --cluster -w table

# Off-cluster snapshot before risky operations
kubectl exec -n kube-system etcd-master1.${SECRET_DOMAIN} -- etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  snapshot save /var/lib/etcd/snapshot-$(date +%Y%m%d).db
scp root@master1:/var/lib/etcd/snapshot-*.db ~/cluster-backups/etcd/

# Defrag (run via the operator-installed CronJob, or one-off)
./tools/etcd-defrag.sh
```

If one voter shows `slow_apply_total` 10× higher than its peers, the underlying disk is the suspect — see [`master1_etcd_disk_swap.md`](master1_etcd_disk_swap.md).

## Resource pressure

```sh
# Per-node CPU/memory utilization
kubectl top nodes
kubectl top pods -A --sort-by=memory | head
kubectl top pods -A --sort-by=cpu | head

# Find OOMKilled pods in the cluster
kubectl get pods -A -o json | \
  jq -r '.items[] | select(.status.containerStatuses // [] | any(.lastState.terminated.reason == "OOMKilled")) | "\(.metadata.namespace)/\(.metadata.name)"'

# Per-pod resource requests vs limits
kubectl describe pod -n <ns> <pod> | grep -A2 -E 'Limits|Requests'
```

Resource philosophy: [`limits.md`](limits.md) — request CPU, don't limit CPU; memory limit = memory request.

## Image / registry

```sh
# Why is this image failing to pull?
kubectl -n <ns> describe pod <pod> | grep -E 'Failed|ErrImagePull|ImagePullBackOff' -A2

# Verify a specific image is reachable from a node
ssh root@<node> 'crictl pull <image>'

# Renovate / dependency state
gh pr list --label renovate --limit 20
```

## NFS

```sh
# From any node — verify the export
showmount -e ${NFS_HOST_0}
showmount -e ${NFS_HOST_2}

# Inside a pod that mounts NFS — verify the mount works
kubectl -n <ns> exec <pod> -- stat /mnt/...

# Slow NFS? Check on the server, not in k8s
ssh root@${NFS_HOST_0} 'nfsstat -s'
ssh root@${NFS_HOST_0} 'nfsiostat 1 5'
```

Background reading: <https://www.redhat.com/sysadmin/using-nfsstat-nfsiostat>.

## External access (out-of-band)

If the cluster is fully borked and you need to reach a node from outside the LAN:

```sh
# OOB SSH path via brain (the home router)
ssh -p 3231 root@173.69.136.210
```

That's the only path that doesn't depend on wg-easy being healthy (which depends on the cluster).

## When to escalate

If a problem isn't yielding to direct inspection:

- **AlertManager + HolmesGPT** are wired to do automated root-cause analysis on every firing alert — check the most recent Pushover ping or Zulip alert thread for HolmesGPT's investigation report before going deeper.
- **mdBook docs** elsewhere in `docs/src/` — `cluster_upgrade.md` has a deep failure-modes table from the 1.34 → 1.35 upgrade that captures most cri-o / kubelet / Longhorn / Rook failure shapes.

## External references

- Kubernetes DNS debugging: <https://kubernetes.io/docs/tasks/administer-cluster/dns-debugging-resolution/>
- Port-forward troubleshooting: <https://kubernetes.io/docs/tasks/access-application-cluster/port-forward-access-application-cluster/>
- netshoot toolbox: <https://github.com/nicolaka/netshoot>
- NFS stats: <https://www.redhat.com/sysadmin/using-nfsstat-nfsiostat>
