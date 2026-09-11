"""Shared Redis-backed queue used by both the Dispatcher and the Panel.

Redis is the single source of truth for queue *order* and status metadata.
The Dispatcher is the only writer of request bodies/results (kept in its own
process memory, never in Redis) and the only reader that pops from the
pending list. The Panel only reads/reorders the pending list and reads the
audit log; it never touches request bodies.
"""
import json
import time

import redis.asyncio as redis

PENDING_KEY = "dispatcher:queue:pending"
RUNNING_KEY = "dispatcher:queue:running"
META_KEY_PREFIX = "dispatcher:queue:meta:"
AUDIT_KEY = "dispatcher:audit"
CONCURRENCY_KEY = "dispatcher:concurrency"
DONE_TTL = 300  # seconds a finished request's metadata stays visible to the panel
AUDIT_MAX_ENTRIES = 500


class Queue:
    def __init__(self, redis_url):
        self.redis = redis.from_url(redis_url, decode_responses=True)

    async def enqueue(self, request_id, user, model):
        meta = {
            "id": request_id,
            "user": user,
            "model": model or "",
            "status": "queued",
            "submitted_at": time.time(),
        }
        await self.redis.hset(META_KEY_PREFIX + request_id, mapping=meta)
        await self.redis.rpush(PENDING_KEY, request_id)

    async def dequeue_blocking(self, timeout=1):
        """Pop the next request id off the front of the pending list, or None
        if nothing arrived within `timeout` seconds."""
        result = await self.redis.blpop(PENDING_KEY, timeout=timeout)
        if result is None:
            return None
        _, request_id = result
        return request_id

    async def mark_running(self, request_id):
        await self.redis.sadd(RUNNING_KEY, request_id)
        await self.redis.hset(
            META_KEY_PREFIX + request_id,
            mapping={"status": "running", "started_at": time.time()},
        )

    async def mark_done(self, request_id, status):
        await self.redis.srem(RUNNING_KEY, request_id)
        key = META_KEY_PREFIX + request_id
        await self.redis.hset(key, mapping={"status": status, "finished_at": time.time()})
        await self.redis.expire(key, DONE_TTL)

    async def list_pending(self):
        ids = await self.redis.lrange(PENDING_KEY, 0, -1)
        items = []
        for rid in ids:
            meta = await self.redis.hgetall(META_KEY_PREFIX + rid)
            if meta:
                items.append(meta)
        return items

    async def status_for_user(self, user):
        """('running', None) if user has an in-flight request, ('queued', N)
        if their earliest pending request sits at 1-based position N, or
        ('idle', None) if neither. Used by the in-page queue-position widget
        (see singleuser/queue_status_proxy.py) -- a small, targeted lookup,
        not a full queue dump, since that's all a single user's own status
        bar needs."""
        for rid in await self.redis.smembers(RUNNING_KEY):
            meta = await self.redis.hgetall(META_KEY_PREFIX + rid)
            if meta.get("user") == user:
                return "running", None
        pending = await self.redis.lrange(PENDING_KEY, 0, -1)
        for position, rid in enumerate(pending, start=1):
            meta = await self.redis.hgetall(META_KEY_PREFIX + rid)
            if meta.get("user") == user:
                return "queued", position
        return "idle", None

    async def get_concurrency(self, default):
        value = await self.redis.get(CONCURRENCY_KEY)
        if value is None:
            await self.redis.set(CONCURRENCY_KEY, default)
            return default
        try:
            return max(1, int(value))
        except ValueError:
            return default

    async def set_concurrency(self, value, actor):
        value = max(1, int(value))
        await self.redis.set(CONCURRENCY_KEY, value)
        await self.audit(actor, "set_concurrency", {"value": value})
        return value

    async def reorder(self, ordered_ids, actor):
        """Rewrite the pending list to match `ordered_ids` as closely as
        possible. Ids no longer in the list (already dequeued by a worker)
        are silently dropped instead of erroring, so an in-flight request can
        never be pulled back into the queue. Any id present in the live list
        but missing from `ordered_ids` is appended at the end rather than
        dropped, so a stale client payload can't silently vanish a request.
        Uses WATCH/MULTI/EXEC so a concurrent dequeue can't be raced.
        """
        async with self.redis.pipeline() as pipe:
            while True:
                await pipe.watch(PENDING_KEY)
                current = await self.redis.lrange(PENDING_KEY, 0, -1)
                current_set = set(current)
                new_order = [rid for rid in ordered_ids if rid in current_set]
                seen = set(new_order)
                new_order += [rid for rid in current if rid not in seen]
                pipe.multi()
                pipe.delete(PENDING_KEY)
                if new_order:
                    pipe.rpush(PENDING_KEY, *new_order)
                try:
                    await pipe.execute()
                    break
                except redis.WatchError:
                    continue
        await self.audit(actor, "reorder", {"order": new_order})
        return new_order

    async def audit(self, actor, action, details=None):
        entry = {"actor": actor, "action": action, "at": time.time(), "details": details or {}}
        await self.redis.rpush(AUDIT_KEY, json.dumps(entry))
        await self.redis.ltrim(AUDIT_KEY, -AUDIT_MAX_ENTRIES, -1)

    async def list_audit(self, limit=100):
        raw = await self.redis.lrange(AUDIT_KEY, -limit, -1)
        return [json.loads(item) for item in raw]
