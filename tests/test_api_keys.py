"""Integration tests for common/api_keys.py against a real Redis.

Skipped automatically if the `redis` package or a reachable Redis server
isn't available -- see tests/test_dispatch_queue.py for why (this sandbox's
.venv has no pip and always skips; run with `pip install redis` and a real
Redis, e.g. `docker run --rm -p 6379:6379 redis:7-alpine`).
"""
import asyncio
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'common'))

try:
    import redis.asyncio as redis_asyncio
    from api_keys import ApiKeyStore, AUTO_PROVISIONED_LABEL
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
class ApiKeyStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = redis_asyncio.from_url(REDIS_URL, decode_responses=True)
        await self.redis.flushdb()
        self.store = ApiKeyStore(self.redis)

    async def asyncTearDown(self):
        await self.redis.flushdb()
        await self.redis.aclose()

    async def test_issued_key_verifies_to_its_owner(self):
        raw_key, meta = await self.store.issue('alice', label='cli', issued_by='alice')
        self.assertEqual(await self.store.verify(raw_key), 'alice')
        self.assertEqual(meta['user'], 'alice')

    async def test_unknown_key_does_not_verify(self):
        self.assertIsNone(await self.store.verify('dsp_not_a_real_key'))
        self.assertIsNone(await self.store.verify(''))
        self.assertIsNone(await self.store.verify(None))

    async def test_revoked_key_stops_verifying(self):
        raw_key, meta = await self.store.issue('alice', label='cli', issued_by='alice')
        self.assertEqual(await self.store.verify(raw_key), 'alice')
        revoked = await self.store.revoke(meta['id'])
        self.assertTrue(revoked)
        self.assertIsNone(await self.store.verify(raw_key))

    async def test_expired_key_does_not_verify(self):
        raw_key, meta = await self.store.issue('alice', label='temp', issued_by='alice', expires_in=-1)
        self.assertIsNone(await self.store.verify(raw_key))

    async def test_revoking_unknown_key_id_returns_false(self):
        self.assertFalse(await self.store.revoke('nonexistent'))

    async def test_verify_updates_last_used_at(self):
        raw_key, meta = await self.store.issue('alice', label='cli', issued_by='alice')
        self.assertEqual(meta['last_used_at'], '')
        await self.store.verify(raw_key)
        refreshed = await self.store.get(meta['id'])
        self.assertNotEqual(refreshed['last_used_at'], '')

    async def test_list_for_user_only_shows_that_users_keys(self):
        await self.store.issue('alice', label='a1', issued_by='alice')
        await self.store.issue('alice', label='a2', issued_by='alice')
        await self.store.issue('bob', label='b1', issued_by='bob')
        alice_keys = await self.store.list_for_user('alice')
        self.assertEqual({k['label'] for k in alice_keys}, {'a1', 'a2'})
        bob_keys = await self.store.list_for_user('bob')
        self.assertEqual({k['label'] for k in bob_keys}, {'b1'})

    async def test_list_all_includes_every_user(self):
        await self.store.issue('alice', label='a1', issued_by='alice')
        await self.store.issue('bob', label='b1', issued_by='bob')
        all_keys = await self.store.list_all()
        self.assertEqual({k['user'] for k in all_keys}, {'alice', 'bob'})

    async def test_revoke_auto_keys_only_revokes_auto_labeled_keys(self):
        _, auto_meta = await self.store.issue('alice', label=AUTO_PROVISIONED_LABEL, issued_by='system')
        manual_raw, manual_meta = await self.store.issue('alice', label='my laptop', issued_by='alice')

        await self.store.revoke_auto_keys('alice')

        auto_refreshed = await self.store.get(auto_meta['id'])
        self.assertEqual(auto_refreshed['revoked'], '1')
        self.assertEqual(await self.store.verify(manual_raw), 'alice')  # untouched
        manual_refreshed = await self.store.get(manual_meta['id'])
        self.assertEqual(manual_refreshed['revoked'], '0')

    async def test_revoke_auto_keys_is_a_noop_when_none_exist(self):
        await self.store.revoke_auto_keys('alice')  # must not raise
        self.assertEqual(await self.store.list_for_user('alice'), [])

    async def test_revoke_auto_keys_does_not_touch_other_users(self):
        _, bob_meta = await self.store.issue('bob', label=AUTO_PROVISIONED_LABEL, issued_by='system')
        await self.store.revoke_auto_keys('alice')
        bob_refreshed = await self.store.get(bob_meta['id'])
        self.assertEqual(bob_refreshed['revoked'], '0')


if __name__ == '__main__':
    unittest.main()
