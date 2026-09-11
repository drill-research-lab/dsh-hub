"""Adapt Harness root-relative resources to a JupyterHub user URL."""
import os
import json
import re
import time
from pathlib import Path

RESOURCE_URL = re.compile(r'''(["'`(])/(plugins|assets|open-in-app)(?=[/"'`?])''')

# jupyter_server_proxy considers the harness process "ready" as soon as its
# port answers *any* HTTP request, which happens before start_harness.py has
# finished exchanging Harness's launch token for a session cookie. Without
# this wait, requests that land in that gap get proxied with no Cookie header
# and Harness answers them as unauthenticated (404). This function is called
# synchronously (no async support in jupyter_server_proxy), so the wait below
# blocks this user's own server briefly rather than the request failing.
COOKIE_WAIT_TIMEOUT = 15
COOKIE_WAIT_INTERVAL = 0.1


def rewrite_response(response):
    content_type = response.headers.get("Content-Type", "").lower()
    if not any(kind in content_type for kind in ("javascript", "json", "text/html", "text/css")):
        return
    service_prefix = os.environ.get("JUPYTERHUB_SERVICE_PREFIX", "/")
    prefix = service_prefix + "harness"
    body = response.body.decode("utf-8")
    if "text/html" in content_type:
        body = body.replace('<base href="/">', f'<base href="{prefix}/">')
        bridge = Path(__file__).with_name("harness_bridge.js").read_text()
        bridge = bridge.replace("__HARNESS_PREFIX__", json.dumps(prefix))
        bridge = bridge.replace("__QUEUE_STATUS_URL__", json.dumps(service_prefix + "queue-status/"))
        body = body.replace("<head>", "<head><script>" + bridge + "</script>", 1)
    body = RESOURCE_URL.sub(lambda m: m.group(1) + prefix + "/" + m.group(2), body)
    response.body = body.encode("utf-8")
    response.headers.pop("Content-Length", None)
    response.headers.pop("ETag", None)
    response.headers["Cache-Control"] = "no-store"


def request_headers(port):
    cookie_file = Path(f"/tmp/dsh-proxy-{port}.cookie")
    deadline = time.monotonic() + COOKIE_WAIT_TIMEOUT
    cookie = ""
    while True:
        try:
            cookie = cookie_file.read_text()
        except FileNotFoundError:
            cookie = ""
        if cookie or time.monotonic() >= deadline:
            break
        time.sleep(COOKIE_WAIT_INTERVAL)
    return {
        "Host": f"127.0.0.1:{port}",
        "Origin": f"http://127.0.0.1:{port}",
        "Accept-Encoding": "identity",
        "Cookie": cookie,
    }
