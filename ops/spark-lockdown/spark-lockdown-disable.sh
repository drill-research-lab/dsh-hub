#!/usr/bin/env bash
# Reverts spark-lockdown-enable.sh: removes every DOCKER-USER rule that
# pair of scripts added (matched by comment tag, not by the specific IP/
# subnet values used when they were added), so this always fully undoes
# the lockdown even if the dispatcher container's IP has changed since.
#
# Verified indirectly on the production Proxmox VM: enable.sh calls this
# with --quiet on every run, and re-running enable.sh after a full stack
# rebuild (new dispatcher IP, old rules for the previous IP still present
# in DOCKER-USER) left exactly 2 rules behind, not 4 -- proof the cleanup
# here actually ran and worked. Not yet exercised as a standalone "turn
# the lockdown off entirely and leave it off" call.
#
# Usage: ./spark-lockdown-disable.sh [--quiet]

set -euo pipefail

COMMENT_TAG="dsh-spark-lockdown"
QUIET=0
[[ "${1:-}" == "--quiet" ]] && QUIET=1

if [[ $EUID -ne 0 ]]; then
  echo "Must run as root (iptables needs it)." >&2
  exit 1
fi

removed=0
# Repeatedly delete the first matching rule by line number until none are
# left, re-listing each time -- deleting by a batch of line numbers in one
# pass would shift every later index out from under itself.
while rule="$(iptables -L DOCKER-USER -n --line-numbers | awk -v tag="$COMMENT_TAG" '$0 ~ tag {print $1; exit}')" && [[ -n "$rule" ]]; do
  iptables -D DOCKER-USER "$rule"
  removed=$((removed + 1))
done

if [[ $QUIET -eq 0 ]]; then
  echo "Removed $removed rule(s) tagged '$COMMENT_TAG'. Spark lockdown is now OFF."
fi
