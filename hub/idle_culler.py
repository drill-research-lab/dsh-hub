"""Stops idle singleuser servers automatically.

last_activity alone can't tell "user is reading a long response" apart from
"actually done and gone" -- and worse, it can't tell "idle by HTTP traffic"
apart from "DSH is mid-generation and just hasn't sent a new proxied request
recently". So before stopping anyone's server, this also asks the Dispatcher
(via the same Redis-backed Queue used by the in-page queue-status widget,
see common/dispatch_queue.py) whether that user currently has a request
running or queued, and skips them if so -- last_activity decides *who to
look at*, Dispatcher's queue state decides *whether it's actually safe*.

Runs as a JupyterHub-managed Service (see jupyterhub_config.py's
c.JupyterHub.services), so JUPYTERHUB_API_TOKEN/JUPYTERHUB_API_URL are
injected automatically and Hub restarts it if it ever dies.
"""
import asyncio
import datetime
import os
import sys
import time

import requests

sys.path.insert(0, "/srv/jupyterhub")
from dispatch_queue import Queue  # noqa: E402

API_URL = os.environ["JUPYTERHUB_API_URL"]
API_TOKEN = os.environ["JUPYTERHUB_API_TOKEN"]
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
IDLE_TIMEOUT_SECONDS = int(os.environ.get("IDLE_CULL_TIMEOUT_MINUTES", "120")) * 60
CHECK_INTERVAL_SECONDS = int(os.environ.get("IDLE_CULL_CHECK_INTERVAL_MINUTES", "15")) * 60

HEADERS = {"Authorization": f"token {API_TOKEN}"}


def _parse_last_activity(value):
    if not value:
        return None
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


async def cull_once(queue):
    resp = requests.get(f"{API_URL}/users", headers=HEADERS, timeout=15)
    resp.raise_for_status()
    now = time.time()

    for user in resp.json():
        name = user.get("name")
        for server_name, server in (user.get("servers") or {}).items():
            if not server.get("ready"):
                continue  # still starting/stopping -- leave it alone
            idle_since = _parse_last_activity(server.get("last_activity"))
            if idle_since is None:
                continue
            idle_for = now - idle_since
            if idle_for < IDLE_TIMEOUT_SECONDS:
                continue

            status, _position = await queue.status_for_user(name)
            if status != "idle":
                print(
                    f"idle-culler: {name} idle {idle_for:.0f}s but Dispatcher says "
                    f"'{status}' -- leaving it running",
                    flush=True,
                )
                continue

            url = f"{API_URL}/users/{name}/server"
            if server_name:
                url = f"{API_URL}/users/{name}/servers/{server_name}"
            del_resp = requests.delete(url, headers=HEADERS, timeout=30)
            if del_resp.status_code in (202, 204):
                print(f"idle-culler: stopped {name}'s server after {idle_for:.0f}s idle", flush=True)
            else:
                print(
                    f"idle-culler: failed to stop {name}'s server: "
                    f"{del_resp.status_code} {del_resp.text}",
                    flush=True,
                )


async def main():
    print(
        f"idle-culler: checking every {CHECK_INTERVAL_SECONDS}s, "
        f"culling servers idle (by HTTP traffic) for more than {IDLE_TIMEOUT_SECONDS}s "
        f"and confirmed idle by the Dispatcher's own queue state",
        flush=True,
    )
    queue = Queue(REDIS_URL)
    while True:
        try:
            await cull_once(queue)
        except Exception as exc:  # noqa: BLE001 -- keep the loop alive across transient errors
            print(f"idle-culler: error during check cycle: {exc}", flush=True)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
