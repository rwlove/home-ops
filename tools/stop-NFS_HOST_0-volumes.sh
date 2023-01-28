#!/bin/bash

pvs=`kubectl get pv -l type=nfs -o jsonpath='{.items[*].metadata.name}'`

# pass in pv name
function get_pvc_ns_from_pv()
{
    ns=`kubectl get pv $1 -o json | jq -r '.spec.claimRef.namespace'`
    echo $ns
}

# pass in pv name
function get_pvc_name_from_pv()
{
    name=`kubectl get pv $1 -o json | jq -r '.spec.claimRef.name'`
    echo $name
}

# pass in pvc ns and name
function get_pod_from_pvc()
{
    pod=`kubectl -n $1 describe pvc $2 | grep Used | grep Used | cut -d ":" -f 2 | tr -d "[:space:]"`
    echo $pod
}

# pass in ns and pod name
# return 'statefulset', 'deployment'
function get_pod_type()
{
    controll=`kubectl -n $1 describe pods $2 | grep "Controlled By"`

    rs=`echo $controll | grep -c ReplicaSet`
    ss=`echo $controll | grep -c StatefulSet`
    #echo "rs: $rs, ss: $ss"
    if [ $rs -gt 0 ] ; then
    echo "replicaset"
    elif [ $ss -gt 0 ] ; then
    echo "statefulset"
    else
    echo "failed"
    fi
}

# pass in ns and pod_name
function remove_replicaset()
{
    dep_name=`echo $2 | sed -r 's/(-[^-]+){2}$//g'`
    #echo "kubectl -n $1 scale --replicas=0 deployment $dep_name"
    kubectl -n $1 scale --replicas=0 deployment $dep_name
}

# pass in ns and pod_name
function remove_statefulset()
{
    ss_name=`echo $2 | sed -r 's/(-[^-]+){1}$//g'`
    #echo "kubectl -n $1 scale --replicas=0 statefulset $ss_name"
    kubectl -n $1 scale --replicas=0 statefulset $ss_name
}

# pass in ns and pvc_name
function remove_pvc()
{
    #echo "kubectl -n $1 delete pvc $2"
    kubectl -n $1 delete pvc $2
}

# pass in pv
function remove_pv()
{
    #echo "kubectl delete pv $1"
    kubectl delete pv $1
}

for pv in $pvs ; do
    server=`kubectl get pv $pv -o jsonpath='{..server}'`

    [ "$server" != "brain.thesteamedcrab.com" ] && continue
    #echo "evaluating - pv: $pv, server: $server"

    #if [ "$server" != "brain.thesteamedcrab.com" ] ; then
    #    echo "rejecting - pv: $pv, server: $server"
    #    continue
    #fi

    ns=$(get_pvc_ns_from_pv $pv)
    pvc_name=$(get_pvc_name_from_pv $pv)
    pod_name=$(get_pod_from_pvc $ns $pvc_name)
    controller=$(get_pod_type $ns $pod_name)

    if [ "$controller" == "statefulset" ] ; then
    remove_statefulset $ns $pod_name
    elif [ "$controller" == "replicaset" ] ; then
    remove_replicaset $ns $pod_name
    else
    echo -n "ERROR "
    fi

    remove_pvc $ns $pvc_name
    remove_pv $pv

    #echo "pv: $pv, server: $server, pod: $pod_name, controller: $controller"
    echo "ns: $ns, pod: $pod_name, controller: $controller"
done
