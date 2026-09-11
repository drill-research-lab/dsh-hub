"""Same-origin relay so the in-page queue-status widget (injected by
harness_bridge.js) can check this user's own Dispatcher queue position
without ever seeing DISPATCHER_API_KEY. The browser can't be trusted with
that key (view-source/devtools would expose it), so this tiny server-side
hop -- which already holds the key as a container env var -- makes the real
call and forwards back only {status, position}.

Proxied by jupyter_server_proxy (see jupyter_server_config.py), so access is
already gated by the same JupyterHub cookie auth as everything else under
/user/<name>/ -- no separate auth needed here.
"""
import argparse
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

DISPATCHER_BASE_URL = os.environ.get("DISPATCHER_BASE_URL", "")
DISPATCHER_API_KEY = os.environ.get("DISPATCHER_API_KEY", "")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep this quiet; jupyter_server_proxy already logs the outer request

    def do_GET(self):
        if not DISPATCHER_BASE_URL or not DISPATCHER_API_KEY:
            self._respond(200, {"status": "unavailable", "position": None})
            return
        req = urllib.request.Request(
            DISPATCHER_BASE_URL + "/queue/position",
            headers={"Authorization": "Bearer " + DISPATCHER_API_KEY},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                self._respond(200, json.loads(resp.read()))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
            self._respond(200, {"status": "unavailable", "position": None})

    def _respond(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
