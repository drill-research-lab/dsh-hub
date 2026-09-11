from harness_proxy import rewrite_response, request_headers

c = get_config()
c.ServerApp.jpserver_extensions = {"jupyter_server_proxy": True}
c.ServerApp.root_dir = "/home/demo"
c.ServerApp.open_browser = False
c.ServerProxy.servers = {
    "harness": {
        "command": ["python", "/opt/demo/start_harness.py", "--port", "{port}"],
        "timeout": 180,
        "absolute_url": False,
        "request_headers_override": request_headers,
        "rewrite_response": rewrite_response,
        "launcher_entry": {"title": "DeepSeek Harness"},
    },
    # Same-origin relay for the in-page queue-position widget (see
    # harness_bridge.js). Not launcher-visible; it's a JSON API, not a page.
    "queue-status": {
        "command": ["python", "/opt/demo/queue_status_proxy.py", "--port", "{port}"],
        "timeout": 30,
        "absolute_url": False,
    },
}
