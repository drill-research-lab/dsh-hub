#!/usr/bin/env bash
# Adds one DNS rewrite (domain -> IP) to a self-hosted AdGuard Home
# instance via its REST API, so lab members' machines resolve the new
# hostname to the shared Caddy instance's IP internally.
#
# NOT VERIFIED against a real AdGuard Home instance -- this is AdGuard
# Home's documented REST API shape (session login + /control/rewrite/add),
# not something this dev sandbox has an AdGuard instance to test against.
# Verify with adguard-list-dns-rewrites.sh after running this.
#
# Usage:
#   ADGUARD_URL=http://127.0.0.1:3535 \   # run this on Diffie itself, AdGuard listens on 3535 there
#   ADGUARD_USER=<username> ADGUARD_PASS=<password> \
#   ./adguard-add-dns-rewrite.sh dsh.islab.xxx 192.168.101.y
#   (second arg = the Caddy machine's IP, not the dsh-hub VM's)

set -euo pipefail

DOMAIN="${1:?Usage: ./adguard-add-dns-rewrite.sh <domain> <ip>}"
IP="${2:?Usage: ./adguard-add-dns-rewrite.sh <domain> <ip>}"

: "${ADGUARD_URL:?Set ADGUARD_URL, e.g. http://127.0.0.1:3535 (run on Diffie itself)}"
: "${ADGUARD_USER:?Set ADGUARD_USER}"
: "${ADGUARD_PASS:?Set ADGUARD_PASS}"

COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

curl -sf -c "$COOKIE_JAR" -X POST "$ADGUARD_URL/control/login" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"${ADGUARD_USER}\",\"password\":\"${ADGUARD_PASS}\"}"

curl -sf -b "$COOKIE_JAR" -X POST "$ADGUARD_URL/control/rewrite/add" \
  -H "Content-Type: application/json" \
  -d "{\"domain\":\"${DOMAIN}\",\"answer\":\"${IP}\"}"

echo "Added rewrite: ${DOMAIN} -> ${IP}. Verify with:"
echo "  ADGUARD_URL=$ADGUARD_URL ADGUARD_USER=$ADGUARD_USER ADGUARD_PASS=*** ./adguard-list-dns-rewrites.sh"
