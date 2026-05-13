#!/bin/bash

: "${SECRET_DOMAIN:?SECRET_DOMAIN must be set}"

curl https://ollama.${SECRET_DOMAIN}/api/pull -d '{
  "name": "deepseek-r1:8b"
}'

#curl https://ollama.${SECRET_DOMAIN}/api/pull -d '{
#  "name": "llava-llama3:8b"
#}'

curl https://ollama.${SECRET_DOMAIN}/api/pull -d '{
  "name": "gemma3:4b"
}'

curl https://ollama.${SECRET_DOMAIN}/api/pull -d '{
  "name": "llama3.2"
}'

curl https://ollama.${SECRET_DOMAIN}/api/tags | jq
