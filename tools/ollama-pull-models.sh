#!/bin/bash
curl https://ollama.thesteamedcrab.com/api/pull -d '{
  "name": "deepseek-r1:8b"
}'

curl https://ollama.thesteamedcrab.com/api/pull -d '{
  "name": "llava-llama3:8b"
}'

curl https://ollama.thesteamedcrab.com/api/pull -d '{
  "name": "llama3.1"
}'

#curl https://ollama.thesteamedcrab.com/api/pull -d '{
#  "name": "llama2-uncensored:latest"
#}'

#curl https://ollama.thesteamedcrab.com/api/pull -d '{
#  "name": "llama2"
#}'

#curl https://ollama.thesteamedcrab.com/api/pull -d '{
#  "name": "llama3.1"
#}'

#curl https://ollama.thesteamedcrab.com/api/pull -d '{
#  "name": "dolphin-mixtral"
#}'

#curl https://ollama.thesteamedcrab.com/api/pull -d '{
#  "name": "acon96/Home-3B-v3-GGUF"
#}'

#curl https://ollama.thesteamedcrab.com/api/pull -d '{
#  "name": "mxbai-embed-large"
#}'

# Obsidian Excalidraw: dall-e-3
#curl https://ollama.thesteamedcrab.com/api/pull -d '{
#  "name": "ehristoforu/dalle-3-xl-v2"
#}'

# Obsidian Excalidraw: gpt-3.5-turbo-1106
#curl https://ollama.thesteamedcrab.com/api/pull -d '{
#  "name": "gpt-3.5-turbo"
#}'

# Obsidian Excalidraw: gpt-4-vision-preview
#curl https://ollama.thesteamedcrab.com/api/pull -d '{
#  "name": "playground_with_gpt-4-vision-preview"
#}'

curl https://ollama.thesteamedcrab.com/api/tags | jq

#curl https://ollama.thesteamedcrab.com/api/generate -d '{
#  "model": "llama2",
#  "prompt": "Why is the sky blue?"
#}'
