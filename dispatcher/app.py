"""Thin OpenAI-compatible gatekeeper in front of the Spark vLLM endpoint.

Requests are enqueued in Redis (shared with the Panel, which is the only
thing allowed to reorder the queue) and held here until a worker slot is
free, then forwarded to Spark. The HTTP request from the caller stays open
the whole time -- from the container's point of view this looks exactly like
calling a normal (slow, single) inference endpoint.

Every response is proxied to the caller as raw bytes, as they arrive from
Spark, over chunked transfer encoding -- this isn't optional: real callers
(dsh included) send `stream: true` and expect a live SSE token stream, not a
single buffered JSON blob at the end. A non-streaming JSON caller still
works fine against a chunked response; HTTP clients reassemble it
transparently. Dispatcher never needs to branch on the caller's `stream`
flag except to pick the Content-Type it advertises.

Identity: every caller (container or otherwise) authenticates with a real
API key, `Authorization: Bearer dsp_...`. Containers get one automatically
at spawn time (see hub/jupyterhub_config.py's pre_spawn_start hook); nothing
here trusts a self-reported username -- the identity attached to a queued
request is always the key's actual owner, looked up server-side.
"""
import asyncio
import json
import os
import sys
import uuid

import tornado.ioloop
import tornado.web
from tornado.httpclient import AsyncHTTPClient, HTTPClientError, HTTPRequest

sys.path.insert(0, "/opt/common")
from api_keys import ApiKeyStore
from dispatch_queue import Queue

SPARK_BASE_URL = os.environ["SPARK_BASE_URL"].rstrip("/")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
DEFAULT_CONCURRENCY = int(os.environ.get("DISPATCHER_CONCURRENCY", "1"))
REQUEST_TIMEOUT = float(os.environ.get("DISPATCHER_REQUEST_TIMEOUT", "300"))
PORT = int(os.environ.get("PORT", "8080"))


def _bearer_token(handler):
    header = handler.request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[len("bearer "):].strip()
    return ""


class ChatCompletionsHandler(tornado.web.RequestHandler):
    def initialize(self, queue, pending_channels, pending_bodies, api_keys, running_tasks):
        self.queue = queue
        self.pending_channels = pending_channels
        self.pending_bodies = pending_bodies
        self.api_keys = api_keys
        self.running_tasks = running_tasks
        self.request_id = None

    def on_connection_close(self):
        # Tornado calls this the moment the caller disconnects (browser tab
        # closed, "stop" button aborts the fetch, network drop) -- even
        # mid-stream, while process_one() is still forwarding tokens from
        # Spark. That worker runs as its own independent asyncio task (see
        # worker_loop), so without this it has no way to learn the caller
        # is gone and just keeps running Spark's response to completion (or
        # the 300s timeout), holding a concurrency slot the whole time for
        # output nobody will ever see. Cancel it so the slot frees up
        # immediately and Spark's own connection gets torn down too.
        if self.request_id is not None:
            task = self.running_tasks.get(self.request_id)
            if task is not None and not task.done():
                task.cancel()

    async def post(self):
        user = await self.api_keys.verify(_bearer_token(self))
        if not user:
            self.set_status(401)
            self.finish(json.dumps({"error": {"message": "missing or invalid API key"}}))
            return
        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            self.set_status(400)
            self.finish(json.dumps({"error": {"message": "invalid JSON body"}}))
            return

        request_id = uuid.uuid4().hex
        self.request_id = request_id
        channel = asyncio.Queue()
        self.pending_channels[request_id] = channel
        self.pending_bodies[request_id] = body
        await self.queue.enqueue(request_id, user=user, model=body.get("model", ""))

        content_type = "text/event-stream" if body.get("stream") else "application/json"
        try:
            while True:
                kind, payload = await channel.get()
                if kind == "status":
                    self.set_status(payload)
                    self.set_header("Content-Type", content_type)
                    if content_type == "text/event-stream":
                        self.set_header("Cache-Control", "no-cache")
                elif kind == "chunk":
                    self.write(payload)
                    await self.flush()
                elif kind == "done":
                    break
        finally:
            self.pending_channels.pop(request_id, None)
            self.pending_bodies.pop(request_id, None)
        self.finish()


class ModelsHandler(tornado.web.RequestHandler):
    def initialize(self, api_keys):
        self.api_keys = api_keys

    async def get(self):
        if not await self.api_keys.verify(_bearer_token(self)):
            self.set_status(401)
            self.finish(json.dumps({"error": {"message": "missing or invalid API key"}}))
            return
        client = AsyncHTTPClient()
        try:
            resp = await client.fetch(SPARK_BASE_URL + "/models")
            self.set_header("Content-Type", "application/json")
            self.finish(resp.body)
        except HTTPClientError as exc:
            self.set_status(exc.code or 502)
            self.finish(json.dumps({"error": {"message": str(exc)}}))


class QueuePositionHandler(tornado.web.RequestHandler):
    """Lets a caller check their own queue status -- not the full queue,
    just their own position, so this can be exposed to a lower-trust,
    same-origin browser widget without handing out anyone else's data."""
    def initialize(self, queue, api_keys):
        self.queue = queue
        self.api_keys = api_keys

    async def get(self):
        user = await self.api_keys.verify(_bearer_token(self))
        if not user:
            self.set_status(401)
            self.finish(json.dumps({"error": {"message": "missing or invalid API key"}}))
            return
        status, position = await self.queue.status_for_user(user)
        self.set_header("Content-Type", "application/json")
        self.finish(json.dumps({"status": status, "position": position}))


class HealthHandler(tornado.web.RequestHandler):
    def get(self):
        self.finish({"status": "ok"})


async def process_one(queue, request_id, body, channel, state):
    status_sent = False
    final_status = 502
    final_label = "error"
    try:
        await queue.mark_running(request_id)

        def on_chunk(chunk):
            nonlocal status_sent
            if not status_sent:
                status_sent = True
                channel.put_nowait(("status", 200))
            channel.put_nowait(("chunk", chunk))

        client = AsyncHTTPClient()
        req = HTTPRequest(
            SPARK_BASE_URL + "/chat/completions",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps(body),
            request_timeout=REQUEST_TIMEOUT,
            streaming_callback=on_chunk,
        )
        try:
            resp = await client.fetch(req, raise_error=False)
            final_status = resp.code
            final_label = "done" if final_status < 400 else "error"
            if not status_sent:
                # nothing streamed -- forward the (small, buffered) body as-is,
                # whether that's an error payload or a real non-streamed reply
                channel.put_nowait(("status", resp.code))
                if resp.body:
                    channel.put_nowait(("chunk", resp.body))
        except asyncio.CancelledError:
            # ChatCompletionsHandler.on_connection_close() cancelled us: the
            # caller is gone, so there's no channel reader left to write to
            # and nothing to forward -- just record it and let the cancel
            # propagate (client.fetch()'s own connection to Spark is torn
            # down as part of this task being cancelled).
            final_label = "cancelled"
            raise
        except Exception as exc:  # connection errors, timeouts, etc.
            final_status = 502
            final_label = "error"
            if not status_sent:
                channel.put_nowait(("status", 502))
                error_body = json.dumps({"error": {"message": f"dispatcher upstream error: {exc}"}})
                channel.put_nowait(("chunk", error_body.encode()))
    finally:
        channel.put_nowait(("done", None))
        await queue.mark_done(request_id, status=final_label)
        state["in_flight"] -= 1


async def worker_loop(queue, pending_channels, pending_bodies, running_tasks, state):
    while True:
        limit = await queue.get_concurrency(DEFAULT_CONCURRENCY)
        if state["in_flight"] >= limit:
            await asyncio.sleep(0.2)
            continue
        request_id = await queue.dequeue_blocking(timeout=1)
        if request_id is None:
            continue
        channel = pending_channels.get(request_id)
        body = pending_bodies.get(request_id)
        if channel is None:
            # caller already gave up (disconnected) before we got to it
            await queue.mark_done(request_id, status="abandoned")
            continue
        state["in_flight"] += 1
        task = asyncio.create_task(process_one(queue, request_id, body, channel, state))
        running_tasks[request_id] = task
        task.add_done_callback(lambda _t, rid=request_id: running_tasks.pop(rid, None))


def make_app(queue, pending_channels, pending_bodies, api_keys, running_tasks):
    return tornado.web.Application(
        [
            (r"/v1/chat/completions", ChatCompletionsHandler,
             dict(queue=queue, pending_channels=pending_channels, pending_bodies=pending_bodies,
                  api_keys=api_keys, running_tasks=running_tasks)),
            (r"/v1/models", ModelsHandler, dict(api_keys=api_keys)),
            (r"/v1/queue/position", QueuePositionHandler, dict(queue=queue, api_keys=api_keys)),
            (r"/healthz", HealthHandler),
        ]
    )


async def main():
    queue = Queue(REDIS_URL)
    api_keys = ApiKeyStore(queue.redis)
    pending_channels = {}
    pending_bodies = {}
    running_tasks = {}
    state = {"in_flight": 0}
    app = make_app(queue, pending_channels, pending_bodies, api_keys, running_tasks)
    app.listen(PORT)
    asyncio.create_task(worker_loop(queue, pending_channels, pending_bodies, running_tasks, state))
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
