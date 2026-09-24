#!/bin/sh
# notify.sh — shared "email Rob" helper for the in-cluster agent tier (Tier 1).
#
# A thin, single-purpose sender any local-cron job can call to deliver ONE
# email through the shared smtp-relay's GUARDRAILED agent endpoint
# (smtp-relay.home.svc.cluster.local:2526). The relay — not this script — is
# the security boundary: it locks the envelope sender to agents@<domain>,
# accepts only the one allowlisted recipient, and rate-caps throughput. This
# script just formats the message and hands it over; if it tried to reach some
# other recipient the relay would 550 it.
#
# Usage:
#   notify.sh --subject "<s>" --body-file <f>       # plain text from a file
#   printf '%s' "<text>" | notify.sh --subject "<s>"  # plain text from stdin
#   notify.sh --subject "<s>" --body-file <t> --html-file <h>   # multipart
#   printf '%s' "<html>" | notify.sh --subject "<s>" --html      # html on stdin
#
# When an HTML body is supplied the message is sent as MIME
# multipart/alternative with BOTH a text/plain and a text/html part, so mail
# clients pick the richer one and text-only clients still get readable output.
# Plain text is the default when no HTML is given.
#
# Transport: curl's smtp:// protocol (the docker.io/alpine/k8s image's curl is
# built with smtp/smtps support — verified). Fails LOUD on any SMTP error so a
# calling cron surfaces the failure instead of silently dropping mail.
#
# Flux postBuild runs envsubst in STRICT mode over this repo's manifests, and
# this file is embedded verbatim into a ConfigMap by configMapGenerator. Every
# shell `$` is therefore doubled (`$$`) so the rendered manifest carries a
# literal `$` for /bin/sh — same convention as the cronjob inline scripts and
# the app README. A bare $${VAR} would be read as a Flux substitution.
set -eu

RELAY="smtp-relay.home.svc.cluster.local"
PORT="2526"
# The relay rewrites the envelope sender regardless, but send a correct one
# anyway. SECRET_DOMAIN is injected into the pod env (see the cronjob spec);
# fall back to a neutral literal if it is somehow unset so we never emit a
# bare "$$SECRET_DOMAIN" as an address.
DOMAIN="$${SECRET_DOMAIN:-localhost}"
MAIL_FROM="agents@$${DOMAIN}"
# The single recipient the relay permits. Injected from the smtp-relay secret
# so Rob's address stays out of git; kept identical to what the relay's
# allowlist checks. If unset the relay would reject anyway.
MAIL_RCPT="$${AGENT_NOTIFY_RCPT:-}"

SUBJECT=""
BODY_FILE=""
HTML_FILE=""
HTML_STDIN="no"

die() { echo "notify.sh: FATAL: $$*" >&2; exit 1; }

while [ "$$#" -gt 0 ]; do
    case "$$1" in
        --subject)   SUBJECT="$${2:-}"; shift 2 ;;
        --body-file) BODY_FILE="$${2:-}"; shift 2 ;;
        --html-file) HTML_FILE="$${2:-}"; shift 2 ;;
        --html)      HTML_STDIN="yes"; shift 1 ;;
        *) die "unknown argument: $$1" ;;
    esac
done

[ -n "$$SUBJECT" ] || die "missing --subject"
[ -n "$$MAIL_RCPT" ] || die "AGENT_NOTIFY_RCPT is empty — cannot address mail (Rob must set 1P smtp-relay.agent_notify_rcpt)"

# --- gather the body/bodies -------------------------------------------------
# Plain text: from --body-file, else from stdin (unless stdin is the HTML src).
# HTML: from --html-file, else from stdin when --html is given.
TMP="$$(mktemp -d)"
trap 'rm -rf "$$TMP"' EXIT

TEXT_PART="$$TMP/text"
HTML_PART="$$TMP/html"
HAVE_HTML="no"

if [ "$$HTML_STDIN" = "yes" ]; then
    # HTML comes from stdin; there is no separate plain-text stream, so derive
    # a minimal text/plain fallback by stripping tags (best-effort).
    cat > "$$HTML_PART"
    HAVE_HTML="yes"
    if [ -n "$$BODY_FILE" ]; then
        [ -f "$$BODY_FILE" ] || die "--body-file not found: $$BODY_FILE"
        cat "$$BODY_FILE" > "$$TEXT_PART"
    else
        sed -e 's/<[^>]*>//g' "$$HTML_PART" > "$$TEXT_PART"
    fi
else
    # Plain text is the primary stream.
    if [ -n "$$BODY_FILE" ]; then
        [ -f "$$BODY_FILE" ] || die "--body-file not found: $$BODY_FILE"
        cat "$$BODY_FILE" > "$$TEXT_PART"
    else
        cat > "$$TEXT_PART"
    fi
    if [ -n "$$HTML_FILE" ]; then
        [ -f "$$HTML_FILE" ] || die "--html-file not found: $$HTML_FILE"
        cat "$$HTML_FILE" > "$$HTML_PART"
        HAVE_HTML="yes"
    fi
fi

# --- build the RFC 5322 message --------------------------------------------
MSG="$$TMP/message"
DATE_HDR="$$(date -u '+%a, %d %b %Y %H:%M:%S +0000')"

{
    printf 'From: %s\r\n' "$$MAIL_FROM"
    printf 'To: %s\r\n' "$$MAIL_RCPT"
    printf 'Subject: %s\r\n' "$$SUBJECT"
    printf 'Date: %s\r\n' "$$DATE_HDR"
    printf 'MIME-Version: 1.0\r\n'

    if [ "$$HAVE_HTML" = "yes" ]; then
        # multipart/alternative: text/plain first (least-rich), text/html last
        # (most-rich) per RFC 2046 — clients render the last part they grok.
        BOUNDARY="notify-$$$$-$$(date -u +%s)"
        printf 'Content-Type: multipart/alternative; boundary="%s"\r\n' "$$BOUNDARY"
        printf '\r\n'
        printf -- '--%s\r\n' "$$BOUNDARY"
        printf 'Content-Type: text/plain; charset=UTF-8\r\n'
        printf 'Content-Transfer-Encoding: 8bit\r\n\r\n'
        cat "$$TEXT_PART"
        printf '\r\n'
        printf -- '--%s\r\n' "$$BOUNDARY"
        printf 'Content-Type: text/html; charset=UTF-8\r\n'
        printf 'Content-Transfer-Encoding: 8bit\r\n\r\n'
        cat "$$HTML_PART"
        printf '\r\n'
        printf -- '--%s--\r\n' "$$BOUNDARY"
    else
        printf 'Content-Type: text/plain; charset=UTF-8\r\n'
        printf 'Content-Transfer-Encoding: 8bit\r\n\r\n'
        cat "$$TEXT_PART"
        printf '\r\n'
    fi
} > "$$MSG"

# --- hand off to the relay --------------------------------------------------
# --upload-file - reads the fully-formed message from the file. Fail loud:
# `set -e` + curl's non-zero exit on any SMTP-level rejection (e.g. the relay
# 550-ing a bad recipient) aborts the caller.
curl --silent --show-error --fail \
     --max-time 30 \
     --url "smtp://$${RELAY}:$${PORT}" \
     --mail-from "$$MAIL_FROM" \
     --mail-rcpt "$$MAIL_RCPT" \
     --upload-file "$$MSG" \
  || die "SMTP submission to $${RELAY}:$${PORT} failed"

echo "notify.sh: sent '$$SUBJECT' to the agent relay"
