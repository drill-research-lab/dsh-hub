#!/usr/bin/env bash
# Lock down the Spark vLLM endpoint (design doc section 6: "prevent bypassing
# the queue") so only the Dispatcher container can reach it directly --
# every other container on the shared Docker network (every user's spawned
# container included) gets dropped before it ever leaves this host.
#
# VERIFIED FOR REAL on the production Proxmox VM (native dockerd, real
# root/iptables access -- this repo's own dev sandbox couldn't do this,
# see git history for why): after `enable`, a throwaway container on the
# same network timed out hitting Spark directly
# (`docker run --rm --network dsh-demo curlimages/curl curl -m 5
# http://<spark host>:8888/v1/models` -> "Connection timed out"), while
# the Dispatcher container itself still got a real 200 from the same
# endpoint. Re-verified again after a full stack rebuild (new dispatcher
# IP) to confirm re-running this script picks up the new IP correctly.
#
# Why this rule lives on the Docker host, not the Spark host: Dispatcher
# and every user container share one Docker bridge network (dsh-demo) and
# both reach Spark over the LAN, so from Spark's side both look like the
# same NAT'd source IP -- a firewall on the Spark host can't tell them
# apart. Only *before* that NAT (i.e. right here, on this Docker host) do
# Dispatcher and a user container still have distinct source IPs to filter
# on.
#
# Usage:
#   ./spark-lockdown-enable.sh
# Reads SPARK_HOST/SPARK_PORT from the environment if set, otherwise parses
# them out of SPARK_BASE_URL in this repo's .env. Re-run this any time the
# dispatcher container is recreated -- its IP on the bridge can change, and
# this script always re-resolves it fresh (see spark-lockdown-disable.sh,
# which this calls first to make re-running idempotent instead of stacking
# a second, stale rule pair).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DOCKER_NETWORK="${DOCKER_NETWORK_NAME:-dsh-demo}"
DISPATCHER_CONTAINER="${DISPATCHER_CONTAINER:-dispatcher}"
COMMENT_TAG="dsh-spark-lockdown"

if [[ $EUID -ne 0 ]]; then
  echo "Must run as root (iptables needs it)." >&2
  exit 1
fi

if [[ -z "${SPARK_HOST:-}" || -z "${SPARK_PORT:-}" ]]; then
  SPARK_BASE_URL=""
  if [[ -f "$REPO_ROOT/.env" ]]; then
    SPARK_BASE_URL="$(grep -E '^SPARK_BASE_URL=' "$REPO_ROOT/.env" | tail -1 | cut -d= -f2-)"
  fi
  if [[ -z "$SPARK_BASE_URL" ]]; then
    echo "Set SPARK_HOST and SPARK_PORT, or have SPARK_BASE_URL in $REPO_ROOT/.env" >&2
    exit 1
  fi
  # e.g. http://192.168.101.70:8888/v1 -> host=192.168.101.70 port=8888
  SPARK_HOST="$(echo "$SPARK_BASE_URL" | sed -E 's#^[a-z]+://##; s#[:/].*##')"
  SPARK_PORT="$(echo "$SPARK_BASE_URL" | sed -E 's#^[a-z]+://[^:/]+:?##; s#/.*##')"
  SPARK_PORT="${SPARK_PORT:-80}"
fi

# Find the container by the label `docker compose` itself stamps on every
# container it creates, rather than guessing a name prefix -- the actual
# container name depends on whatever the compose *project* name is (which
# defaults to the directory the repo was cloned into, e.g. "dsh-hub" ->
# "dsh-hub-dispatcher-1"), which this script can't assume and shouldn't
# need to.
container_id="$(docker ps -q --filter "label=com.docker.compose.service=$DISPATCHER_CONTAINER" | head -1)"
DISPATCHER_IP=""
if [[ -n "$container_id" ]]; then
  DISPATCHER_IP="$(docker inspect -f "{{with index .NetworkSettings.Networks \"$DOCKER_NETWORK\"}}{{.IPAddress}}{{end}}" "$container_id" 2>/dev/null || true)"
fi
if [[ -z "$DISPATCHER_IP" ]]; then
  echo "Could not resolve the Dispatcher container's IP on network '$DOCKER_NETWORK'." >&2
  echo "Is it running? If the compose service isn't named 'dispatcher', set DISPATCHER_CONTAINER=<service name>." >&2
  exit 1
fi

NETWORK_SUBNET="$(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' "$DOCKER_NETWORK")"
if [[ -z "$NETWORK_SUBNET" ]]; then
  echo "Could not resolve the '$DOCKER_NETWORK' network's subnet." >&2
  exit 1
fi

"$SCRIPT_DIR/spark-lockdown-disable.sh" --quiet || true

# Order matters: the ACCEPT for Dispatcher must be inserted *before* the
# DROP for the whole subnet -- iptables takes the first matching rule, and
# Dispatcher's own IP is itself inside that subnet.
iptables -I DOCKER-USER 1 -s "$DISPATCHER_IP" -d "$SPARK_HOST" -p tcp --dport "$SPARK_PORT" -j ACCEPT -m comment --comment "$COMMENT_TAG"
iptables -I DOCKER-USER 2 -s "$NETWORK_SUBNET" -d "$SPARK_HOST" -p tcp --dport "$SPARK_PORT" -j DROP -m comment --comment "$COMMENT_TAG"

echo "Locked down: only $DISPATCHER_IP (dispatcher) may reach $SPARK_HOST:$SPARK_PORT from $NETWORK_SUBNET."
echo "Re-run this script if the dispatcher container is ever recreated (its IP can change)."
