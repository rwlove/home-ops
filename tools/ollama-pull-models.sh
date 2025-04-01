#!/bin/bash
curl https://ollama.thesteamedcrab.com/api/pull -d '{
  "name": "deepseek-r1:8b"
}'

#curl https://ollama.thesteamedcrab.com/api/pull -d '{
#  "name": "llava-llama3:8b"
#}'

curl https://ollama.thesteamedcrab.com/api/pull -d '{
  "name": "gemma3:4b"
}'

curl https://ollama.thesteamedcrab.com/api/pull -d '{
  "name": "llama3.2"
}'

curl https://ollama.thesteamedcrab.com/api/tags | jq
