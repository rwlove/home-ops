#!/bin/bash

kubectl -n home logs frigate-0 -f | grep Copied | awk '{ print $9"/"$11}' | awk -F '/' '{print $9 " " $7}'
