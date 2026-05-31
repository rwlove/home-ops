#!/usr/bin/env bash
#
# omada-cert-sync.sh — pull the cluster's Let's Encrypt wildcard cert
# onto brain's Omada controller keystore, restart Omada only when the
# cert actually changed. Brain-initiated (Pull); the cluster is a passive
# source. Designed to be a no-op on every tick except the ~quarterly
# renewal day.
#
# INSTALL: this file is NOT Flux-managed. Rob installs it on brain once
# (see README.md). It is checked into the repo for review + history only.
#
# Prime directive: never restart Omada with a missing/invalid cert. Any
# fetch/parse failure => exit 0 and leave the keystore untouched.

set -euo pipefail

# --- config -----------------------------------------------------------
API="https://192.168.6.1:6443"            # kube-vip controlplane VIP
NS="network"
SECRET="thesteamedcrab-com-tls"           # LE wildcard *.thesteamedcrab.com
CONF_DIR="/etc/omada-cert-sync"
TOKEN_FILE="${CONF_DIR}/token"            # SA token (Rob populates)
CA_FILE="${CONF_DIR}/ca.crt"              # cluster CA (Rob populates)

OMADA_DIR="/opt/tplink/EAPController"
KEYSTORE="${OMADA_DIR}/data/keystore/eap.keystore"
ALIAS="eap"
STOREPASS="tplink"                        # Omada keystore default

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

log() { logger -t omada-cert-sync -- "$*"; echo "omada-cert-sync: $*"; }
bail_clean() { log "$*"; exit 0; }   # non-fatal: leave keystore as-is

# --- preconditions ----------------------------------------------------
[[ -r "$TOKEN_FILE" ]] || bail_clean "token file $TOKEN_FILE missing/unreadable"
[[ -r "$CA_FILE"    ]] || bail_clean "CA file $CA_FILE missing/unreadable"
[[ -w "$KEYSTORE"   ]] || bail_clean "keystore $KEYSTORE missing/unwritable"

TOKEN="$(< "$TOKEN_FILE")"

# --- fetch ------------------------------------------------------------
# On unreachable cluster (e.g. down at renewal) curl -f fails -> exit 0.
if ! resp="$(curl -fsS --max-time 15 --cacert "$CA_FILE" \
      -H "Authorization: Bearer ${TOKEN}" \
      "${API}/api/v1/namespaces/${NS}/secrets/${SECRET}" 2>/dev/null)"; then
  bail_clean "cluster API unreachable or secret fetch failed; keystore untouched"
fi

jq -r '.data["tls.crt"] // empty' <<<"$resp" | base64 -d > "$WORK/tls.crt" 2>/dev/null || true
jq -r '.data["tls.key"] // empty' <<<"$resp" | base64 -d > "$WORK/tls.key" 2>/dev/null || true

[[ -s "$WORK/tls.crt" && -s "$WORK/tls.key" ]] || bail_clean "secret missing tls.crt/tls.key; keystore untouched"
openssl x509 -in "$WORK/tls.crt" -noout 2>/dev/null || bail_clean "fetched tls.crt not a valid cert; keystore untouched"
openssl rsa  -in "$WORK/tls.key" -noout 2>/dev/null \
  || openssl pkey -in "$WORK/tls.key" -noout 2>/dev/null \
  || bail_clean "fetched tls.key not a valid key; keystore untouched"

# --- diff -------------------------------------------------------------
new_fp="$(openssl x509 -in "$WORK/tls.crt" -noout -fingerprint -sha256 | sed 's/.*=//')"
cur_fp="$(keytool -list -keystore "$KEYSTORE" -storepass "$STOREPASS" -alias "$ALIAS" 2>/dev/null \
            | grep -i 'SHA-256' | sed 's/.*: //' | tr -d ' ')"
new_fp="$(tr -d ' ' <<<"$new_fp")"

if [[ -n "$cur_fp" && "$new_fp" == "$cur_fp" ]]; then
  bail_clean "cert unchanged (${new_fp}); no action"
fi
log "cert changed (old=${cur_fp:-none} new=${new_fp}); rebuilding keystore"

# --- rebuild ----------------------------------------------------------
openssl pkcs12 -export \
  -in "$WORK/tls.crt" -inkey "$WORK/tls.key" \
  -name "$ALIAS" -out "$WORK/eap.p12" -passout pass:"$STOREPASS"

# Match the existing keystore type (JKS) so Omada reads it unchanged.
keytool -importkeystore -noprompt \
  -srckeystore "$WORK/eap.p12" -srcstoretype PKCS12 -srcstorepass "$STOREPASS" \
  -destkeystore "$WORK/eap.keystore" -deststoretype JKS \
  -deststorepass "$STOREPASS" -destkeypass "$STOREPASS" -alias "$ALIAS"

# --- swap + restart ---------------------------------------------------
cp -a "$KEYSTORE" "${KEYSTORE}.bak.$(date +%Y%m%d%H%M%S)"
install -m 600 -o root -g root "$WORK/eap.keystore" "$KEYSTORE"

log "restarting Omada controller (tpeap restart)"
if tpeap restart >/dev/null 2>&1; then
  log "Omada restarted; new cert active"
else
  log "WARNING: tpeap restart returned non-zero — check 'tpeap status'"
  exit 1
fi
