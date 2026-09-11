"""Integration tests for common/dispatch_queue.py against a real Redis.

Skipped automatically if the `redis` package or a reachable Redis server
isn't available -- this dev sandbox's .venv has no pip, so it always skips
here; run it in an environment with `pip install redis` and Redis reachable
at REDIS_URL (default redis://localhost:6379/0), e.g. via
`docker run --rm -p 6379:6379 redis:7-alpine`.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'common'))

try:
    import redis.asyncio as redis_asyncio
    from dispatch_queue import Queue
    REDIS_IMPORTABLE = True
except ImportError:
    REDIS_IMPORTABLE = False

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')


def _redis_reachable():
    if not REDIS_IMPORTABLE:
        return False
    async def _ping():
        client = redis_asyncio.from_url(REDIS_URL, socket_connect_timeout=1)
        try:
            await client.ping()
            return True
        except Exception:
            return False
        finally:
            await client.aclose()
    try:
        return asyncio.run(_ping())
    except Exception:
        return False


SKIP_REASON = 'redis package or a reachable Redis server not available'
CAN_RUN = _redis_reachable()


@unittest.skipUnless(CAN_RUN, SKIP_REASON)
class DispatchQueueTest(unittest.IsolatedAsyncioTestCase):
    # A single event loop backs asyncSetUp/test/asyncTearDown here, unlike
    # separate asyncio.run() calls, which would each hand the redis client
    # fresh internal locks/futures bound to a *different* loop and crash
    # with "Future attached to a different loop" on the very first command.
    async def asyncSetUp(self):
        self.queue = Queue(REDIS_URL)
        await self.queue.redis.flushdb()

    async def asyncTearDown(self):
        await self.queue.redis.flushdb()
        await self.queue.redis.aclose()

    async def test_enqueue_and_dequeue_is_fifo(self):
        await self.queue.enqueue('a', user='alice', model='m')
        await self.queue.enqueue('b', user='bob', model='m')
        first = await self.queue.dequeue_blocking(timeout=1)
        second = await self.queue.dequeue_blocking(timeout=1)
        self.assertEqual((first, second), ('a', 'b'))

    async def test_reorder_moves_pending_items(self):
        for rid in ('a', 'b', 'c'):
            await self.queue.enqueue(rid, user='alice', model='m')
        result = await self.queue.reorder(['c', 'a', 'b'], actor='admin')
        self.assertEqual(result, ['c', 'a', 'b'])

    async def test_reorder_cannot_recall_an_already_dequeued_item(self):
        for rid in ('a', 'b', 'c'):
            await self.queue.enqueue(rid, user='alice', model='m')
        # 'a' is already sent to a worker (dequeued) before the admin's
        # reorder request lands; trying to move it back to the front must
        # not resurrect it in the pending list.
        popped = await self.queue.dequeue_blocking(timeout=1)
        new_order = await self.queue.reorder([popped, 'c', 'b'], actor='admin')
        self.assertEqual(popped, 'a')
        self.assertNotIn('a', new_order)
        self.assertEqual(new_order, ['c', 'b'])

    async def test_reorder_appends_ids_missing_from_client_payload(self):
        for rid in ('a', 'b', 'c'):
            await self.queue.enqueue(rid, user='alice', model='m')
        # stale client payload omits 'b' -- it must not be silently dropped
        result = await self.queue.reorder(['c', 'a'], actor='admin')
        self.assertEqual(result, ['c', 'a', 'b'])

    async def test_concurrency_default_is_seeded_then_settable(self):
        first = await self.queue.get_concurrency(default=1)
        updated = await self.queue.set_concurrency(3, actor='admin')
        second = await self.queue.get_concurrency(default=1)
        self.assertEqual((first, updated, second), (1, 3, 3))

    async def test_audit_log_records_admin_actions(self):
        await self.queue.enqueue('a', user='alice', model='m')
        await self.queue.reorder(['a'], actor='admin')
        await self.queue.set_concurrency(2, actor='admin')
        entries = await self.queue.list_audit()
        actions = [e['action'] for e in entries]
        self.assertEqual(actions, ['reorder', 'set_concurrency'])
        self.assertTrue(all(e['actor'] == 'admin' for e in entries))


if __name__ == '__main__':
    unittest.main()
