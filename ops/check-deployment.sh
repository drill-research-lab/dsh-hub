#!/usr/bin/env bash
# Read-only health check for a dsh-hub deployment. Run from the repo root
# on the host that runs `docker compose` for this stack. Doesn't change
# anything -- safe to re-run any time you want a snapshot of where things
# stand (e.g. after a git pull + rebuild, or before/after someone logs in).
set -uo pipefail

pass() { echo "  OK    $1"; }
fail() { echo "  FAIL  $1"; }
warn() { echo "  WARN  $1"; }
section() { echo; echo "== $1 =="; }

ENV_FILE="${ENV_FILE:-.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  echo "No $ENV_FILE found in $(pwd) -- run this from the repo root (where .env lives)."
  exit 1
fi

section "Git"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  LOCAL="$(git rev-parse HEAD)"
  if git fetch -q origin main 2>/dev/null; then
    REMOTE="$(git rev-parse origin/main 2>/dev/null || echo "")"
    if [[ -z "$REMOTE" ]]; then
      warn "fetched origin but couldn't resolve origin/main"
    elif [[ "$LOCAL" == "$REMOTE" ]]; then
      pass "up to date with origin/main (${LOCAL:0:12})"
    else
      fail "HEAD (${LOCAL:0:12}) differs from origin/main (${REMOTE:0:12}) -- git pull, then rebuild"
    fi
  else
    warn "couldn't fetch origin/main (no network / no remote?) -- skipping comparison"
  fi
else
  warn "not run from inside a git repo"
fi

section "DNS: LDAP hostname resolution"
if [[ -n "${LDAP_SERVER_ADDRESS:-}" ]]; then
  if resolved="$(getent hosts "$LDAP_SERVER_ADDRESS" 2>/dev/null)"; then
    pass "getent hosts $LDAP_SERVER_ADDRESS -> $(awk '{print $1}' <<<"$resolved")"
  else
    fail "getent hosts $LDAP_SERVER_ADDRESS FAILS -- check systemd-resolved routing domains: resolvectl status <iface> | grep -i domain"
  fi
else
  warn "LDAP_SERVER_ADDRESS not set in $ENV_FILE"
fi

section "LDAP port reachability"
if [[ -n "${LDAP_SERVER_ADDRESS:-}" ]]; then
  PORT="${LDAP_SERVER_PORT:-}"
  if [[ -z "$PORT" ]]; then
    [[ "${LDAP_TLS_STRATEGY:-before_bind}" == "on_connect" ]] && PORT=636 || PORT=389
  fi
  if timeout 3 bash -c "cat < /dev/null > /dev/tcp/${LDAP_SERVER_ADDRESS}/${PORT}" 2>/dev/null; then
    pass "TCP connect to $LDAP_SERVER_ADDRESS:$PORT succeeds"
  else
    fail "TCP connect to $LDAP_SERVER_ADDRESS:$PORT FAILS"
  fi
fi

section "Docker images"
USER_IMG_ID="$(docker images -q dsh-demo-user:local 2>/dev/null)"
if [[ -n "$USER_IMG_ID" ]]; then
  pass "dsh-demo-user:local exists (${USER_IMG_ID})"
else
  fail "dsh-demo-user:local NOT built -- run: docker compose --profile build build user-image"
fi

section "Stale singleuser containers (old image, from before a rebuild)"
# DockerSpawner creates these directly via the Docker API (not through
# `docker compose`), so they carry no com.docker.compose.* labels to filter
# on -- match by the fixed name_template prefix from jupyterhub_config.py
# instead ("dsh-demo-{username}").
mapfile -t SU_CIDS < <(docker ps -aq --filter "name=dsh-demo-" 2>/dev/null)
if [[ "${#SU_CIDS[@]}" -eq 0 ]]; then
  warn "no singleuser containers exist yet (nobody has spawned since this host came up / was last cleaned)"
else
  for cid in "${SU_CIDS[@]}"; do
    name="$(docker inspect -f '{{.Name}}' "$cid" | sed 's#^/##')"
    cimg="$(docker inspect -f '{{.Image}}' "$cid")"
    if [[ -n "$USER_IMG_ID" && "$cimg" != *"$USER_IMG_ID"* ]]; then
      fail "$name is running an OLD image ($cimg) -- current is $USER_IMG_ID; docker rm -f $name to force a fresh spawn on next login"
    else
      pass "$name matches the current image"
    fi
  done
fi

section "Hub container"
if docker compose ps hub 2>/dev/null | grep -q "Up"; then
  pass "hub container is running"
else
  fail "hub container is not running (docker compose ps hub)"
fi

BIND="${HUB_BIND_ADDRESS:-127.0.0.1}"
CODE="$(curl -sk -o /dev/null -w '%{http_code}' "http://${BIND}:9000/hub/login" 2>/dev/null || echo "000")"
if [[ "$CODE" == "200" || "$CODE" == "302" ]]; then
  pass "http://${BIND}:9000/hub/login responds ($CODE)"
else
  fail "http://${BIND}:9000/hub/login returned $CODE"
fi

section "Disk quota"
VOL_COUNT="$(docker volume ls -q --filter name=dsh-demo-home- 2>/dev/null | wc -l | tr -d ' ')"
if [[ "$VOL_COUNT" -eq 0 ]]; then
  warn "no dsh-demo-home-* volumes yet -- nobody has spawned, so xfs_quota enforcement is still unverified"
else
  pass "$VOL_COUNT user home volume(s) exist"
  if command -v xfs_quota >/dev/null 2>&1; then
    echo "  -- xfs project quota report for the Docker data mount (needs sudo) --"
    sudo xfs_quota -x -c 'report -p' /var/lib/docker 2>/dev/null | sed 's/^/  /' || warn "xfs_quota report failed (wrong mount point, or not prjquota-mounted here?)"
  else
    warn "xfs_quota not installed on this host -- can't verify quotas are actually applied"
  fi
fi

section "spark-lockdown"
if [[ -x ops/spark-lockdown/spark-lockdown-status.sh ]]; then
  ops/spark-lockdown/spark-lockdown-status.sh 2>&1 | sed 's/^/  /'
else
  warn "ops/spark-lockdown/spark-lockdown-status.sh not found or not executable"
fi

echo
echo "Done."
