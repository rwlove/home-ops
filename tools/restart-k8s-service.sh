#!/bin/bash

unset ns
unset deployment
unset replicas
unset pods
unset running

while getopts "hrn:d:" arg; do
  case $arg in
    h)
      echo "usgae"
      ;;
    n)
      ns=$OPTARG
      ;;
    d)
      deployment=$OPTARG
      ;;
    r)
      replicas=$OPTARG
      ;;
    \?)
      echo "WRONG" >&2
      ;;
  esac
done

shift "$(( OPTIND - 1 ))"

if [ -z "${ns}" ] || [ -z "${deployment}" ] ; then
        echo 'Missing -n and/or -d options' >&2
        exit 1
fi

[ -z "${replicas}" ] && replicas=1

echo "replicas: $replicas"

kubectl scale --replicas=0 deploy/"${deployment}" -n "${ns}"

pods=`kubectl -n home get pods -l app.kubernetes.io/instance="${deployment}" 2> /dev/null`
if [ -z "${pods}" ] ; then
    unset running
else
    running=`echo "${running}" | grep "${deployment}" | wc -l`
fi

while [ ! -z "${running}" ] ; do
    sleep 1
    pods=`kubectl -n home get pods -l app.kubernetes.io/instance="${deployment}" 2> /dev/null`
    if [ -z "${pods}" ] ; then
	unset running
    else
	running=`echo "${running}" | grep "${deployment}" | wc -l`
    fi

    if [ -z "${running}" ] ; then
	unset running
    fi
done

kubectl scale --replicas="${replicas}" deploy/"${deployment}" -n "${ns}"
