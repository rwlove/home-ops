#!/bin/bash

# kubernetes/main/apps/home/home-assistant/app/helmrelease.yaml

ns=`echo $1 | awk -F'/' '{print $4}'`
hr=`echo $1 | awk -F'/' '{print $5}'`

stg new -m disable-${hr} ; stg mv "${1}" "${1}-disabled"

stg refresh --no-verify ; stg pop -a ; git pull ; stg push -a ; stg commit -a ; stg clean ; git push

flux -n ${ns} delete hr ${hr} -s

kubectl -n ${ns} delete hr ${hr}

git revert --no-edit `git log --oneline | head -n 1 | cut -d " " -f 1`

stg refresh --no-verify ; stg pop -a ; git pull ; stg push -a ; stg commit -a ; stg clean ; git push
