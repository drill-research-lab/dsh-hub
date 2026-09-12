#!/usr/bin/env bash
# Reconciles each user's home-volume disk quota against the desired state
# recorded in Redis (design doc section 3 / common/resource_limits.py's
# disk_mb) -- run this on a timer (systemd timer or cron; see README).
#
# Why this is a separate, periodically-run script instead of a live
# `docker update`-style apply like CPU/memory (panel/app.py's
# ResourceLimitPatchApiHandler._apply_live): XFS project quotas are a HOST
# FILESYSTEM concept with no Docker API surface at all -- there is nothing
# to call through docker-socket-proxy for this, so nothing containerized
# (Panel included) can apply it directly, unlike cpu/memory which Docker
# itself enforces per-container. This script is meant to run ON THE DOCKER
# HOST ITSELF, as root, converging every user's actual quota to match
# Redis each time it runs. A user who just got a new limit from Panel sees
# it "pending until the next reconciler run" rather than instantly.
#
# PARTIALLY VERIFIED FOR REAL on the production Proxmox VM: ran clean
# against a real XFS-with-prjquota mount (`/var/lib/docker` on its own
# 300GB disk) with zero errors -- confirms the container/Redis-lookup
# plumbing (docker ps label filter, docker exec into the hub container,
# ResourceLimitStore.get()) all works end to end on a real host. It ran
# with zero users logged in yet, though, so the loop body -- the actual
# `xfs_quota -x -c 'project ...'`/`limit -p bhard=...` calls -- has never
# executed even once (nothing to iterate over). Re-run this once at least
# one real dsh-demo-home-<user> volume exists and check its output/
# `xfs_quota -x -c 'report -p'` to close that gap.
#
# Requires on the host: `docker` CLI, `xfs_quota` (xfsprogs package), root,
# and the `hub` compose service running (used only to read Redis through
# its already-installed redis client + common/resource_limits.py -- this
# script itself never talks to Docker's API, unlike spark-lockdown.sh).
#
# Usage: sudo XFS_MOUNTPOINT=/var/lib/docker ./apply-disk-quotas.sh

set -euo pipefail

XFS_MOUNTPOINT="${XFS_MOUNTPOINT:-/var/lib/docker}"
VOLUME_PREFIX="${VOLUME_PREFIX:-dsh-demo-home-}"
# Persistent username -> XFS project-id assignment. Project ids are small
# stable integers XFS itself has no naming for, so something has to keep
# this mapping around across runs -- a flat file is enough for a lab-scale
# user count and avoids adding a database just for this.
PROJECT_ID_MAP="${PROJECT_ID_MAP:-/var/lib/dsh-hub/disk-quota-project-ids.tsv}"
PROJECT_ID_START=1000

if [[ $EUID -ne 0 ]]; then
  echo "Must run as root (xfs_quota needs it)." >&2
  exit 1
fi
if ! command -v xfs_quota >/dev/null; then
  echo "xfs_quota not found -- install the xfsprogs package." >&2
  exit 1
fi

mkdir -p "$(dirname "$PROJECT_ID_MAP")"
touch "$PROJECT_ID_MAP"

# Find the container by the label `docker compose` stamps on every
# container it creates, rather than guessing a name prefix -- the actual
# container name depends on whatever the compose *project* name is (which
# defaults to the directory the repo was cloned into, e.g. "dsh-hub" ->
# "dsh-hub-hub-1"), which this script can't assume and shouldn't need to.
if [[ -z "${HUB_CONTAINER:-}" ]]; then
  HUB_CONTAINER="$(docker ps --filter 'label=com.docker.compose.service=hub' --format '{{.Names}}' | head -1)"
fi
if [[ -z "$HUB_CONTAINER" ]]; then
  echo "Could not find the hub container (no container labeled com.docker.compose.service=hub is running)." >&2
  echo "Set HUB_CONTAINER=<name> if the compose service isn't named 'hub'." >&2
  exit 1
fi

next_project_id() {
  local max
  max="$(awk -F'\t' '{print $2}' "$PROJECT_ID_MAP" | sort -n | tail -1)"
  if [[ -z "$max" ]]; then
    echo "$PROJECT_ID_START"
  else
    echo "$((max + 1))"
  fi
}

project_id_for() {
  local user="$1" id
  id="$(awk -F'\t' -v u="$user" '$1 == u {print $2; exit}' "$PROJECT_ID_MAP")"
  if [[ -z "$id" ]]; then
    id="$(next_project_id)"
    printf '%s\t%s\n' "$user" "$id" >> "$PROJECT_ID_MAP"
  fi
  echo "$id"
}

# One JSON object per line: {"user": "...", "disk_mb": N}. Reuses the same
# ResourceLimitStore.get() every other caller uses (Hub's pre_spawn_hook,
# Panel's API), so "what quota should this user have" is computed in
# exactly one place -- this script only ever *applies* the number, it does
# not re-derive defaults/overrides itself.
disk_mb_for() {
  local user="$1"
  docker exec "$HUB_CONTAINER" python3 -c "
import asyncio, json, os, sys
sys.path.insert(0, '/srv/jupyterhub')
import redis.asyncio as redis_asyncio
from resource_limits import ResourceLimitStore

async def main():
    r = redis_asyncio.from_url(os.environ.get('REDIS_URL', 'redis://redis:6379/0'), decode_responses=True)
    store = ResourceLimitStore(r)
    limit = await store.get('$user')
    print(limit['disk_mb'])

asyncio.run(main())
"
}

applied=0
skipped=0
while read -r volume; do
  user="${volume#"$VOLUME_PREFIX"}"
  mountpoint="$(docker volume inspect "$volume" --format '{{.Mountpoint}}' 2>/dev/null || true)"
  if [[ -z "$mountpoint" || ! -d "$mountpoint" ]]; then
    echo "skip $user: could not resolve volume $volume's mountpoint" >&2
    skipped=$((skipped + 1))
    continue
  fi
  disk_mb="$(disk_mb_for "$user")"
  if [[ -z "$disk_mb" ]]; then
    echo "skip $user: could not read desired disk_mb from Redis" >&2
    skipped=$((skipped + 1))
    continue
  fi
  projid="$(project_id_for "$user")"
  xfs_quota -x -c "project -s -p $mountpoint $projid" "$XFS_MOUNTPOINT" >/dev/null
  xfs_quota -x -c "limit -p bhard=${disk_mb}m $projid" "$XFS_MOUNTPOINT"
  echo "applied: $user -> ${disk_mb}MB (project $projid, $mountpoint)"
  applied=$((applied + 1))
done < <(docker volume ls --filter "name=$VOLUME_PREFIX" --format '{{.Name}}')

echo "Done: $applied applied, $skipped skipped."
