#!/usr/bin/env bash
# Generate a fresh OAuth client secret + Argon2 hash for an Authelia OIDC client.
# Usage: tools/gen-oauth-client-secret.sh <app-name>
#   app-name is just the label printed back, e.g. "mealie"
#
# Output:
#   - PLAINTEXT: paste into the 1P <app-name> item, field OAUTH_CLIENT_SECRET
#   - Hash digest: paste into Authelia's OIDC client config (client_secret)
set -euo pipefail

APP=${1:-app}
PLAINTEXT=$(openssl rand -base64 24 | tr -d '/+=')

echo "1P '$APP' item, field OAUTH_CLIENT_SECRET (plaintext):"
echo "  $PLAINTEXT"
echo
echo "Authelia client_secret (paste in chat / configmap):"
docker run --rm ghcr.io/authelia/authelia:4.39.13 \
  authelia crypto hash generate argon2 --password "$PLAINTEXT" |
  awk '/^Digest:/ {print "  " $2}'
