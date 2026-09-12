#!/usr/bin/env bash
# Shows whether spark-lockdown-enable.sh's rules are currently active.
#
# Verified for real on the production Proxmox VM -- ran twice (once after
# the initial enable, once after a full stack rebuild) and both times
# correctly printed the rules actually present in DOCKER-USER.

set -euo pipefail

COMMENT_TAG="dsh-spark-lockdown"

if [[ $EUID -ne 0 ]]; then
  echo "Must run as root (iptables needs it)." >&2
  exit 1
fi

rules="$(iptables -L DOCKER-USER -n --line-numbers | awk -v tag="$COMMENT_TAG" '$0 ~ tag')"
if [[ -z "$rules" ]]; then
  echo "OFF: no spark-lockdown rules present in DOCKER-USER."
  exit 1
fi

echo "ON: spark-lockdown rules present in DOCKER-USER:"
echo "$rules"
