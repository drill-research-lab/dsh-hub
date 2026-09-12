#!/usr/bin/env bash
# Lists current DNS rewrites on an AdGuard Home instance -- use this to
# confirm adguard-add-dns-rewrite.sh actually took effect.
#
# Usage:
#   ADGUARD_URL=http://127.0.0.1:3535 \   # run this on Diffie itself, AdGuard listens on 3535 there
#   ADGUARD_USER=<username> ADGUARD_PASS=<password> \
#   ./adguard-list-dns-rewrites.sh

set -euo pipefail

: "${ADGUARD_URL:?Set ADGUARD_URL, e.g. http://127.0.0.1:3535 (run on Diffie itself)}"
: "${ADGUARD_USER:?Set ADGUARD_USER}"
: "${ADGUARD_PASS:?Set ADGUARD_PASS}"

COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

curl -sf -c "$COOKIE_JAR" -X POST "$ADGUARD_URL/control/login" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"${ADGUARD_USER}\",\"password\":\"${ADGUARD_PASS}\"}"

curl -sf -b "$COOKIE_JAR" "$ADGUARD_URL/control/rewrite/list"
echo
