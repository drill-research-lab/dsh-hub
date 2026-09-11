"""Shared API-key store for Dispatcher identity checks and Panel management.

Keys are never stored in plaintext, only a SHA-256 hash mapping to the
owning user and metadata (design doc section 7). Redis is the single source
of truth, shared between:
- Hub, which mints one key automatically per user at container spawn time
  (see hub/jupyterhub_config.py's pre_spawn_start hook) so every container
  gets a real, unforgeable credential without any manual step,
- Dispatcher, which validates the Authorization header on every request,
- Panel, which lets any logged-in user self-issue/revoke their own keys,
  and lets admins revoke anyone's.
"""
import hashlib
import secrets
import time

KEY_HASH_KEY = "dispatcher:apikeys:hash:"  # + sha256 -> key_id
KEY_META_KEY = "dispatcher:apikeys:meta:"  # + key_id -> metadata hash
USER_KEYS_KEY = "dispatcher:apikeys:byuser:"  # + user -> set of key_ids
ALL_KEYS_SET = "dispatcher:apikeys:all"  # set of all key_ids, for admin listing

AUTO_PROVISIONED_LABEL = "container (auto)"


def _hash(raw_key):
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _generate_raw_key():
    return "dsp_" + secrets.token_hex(24)


class ApiKeyStore:
    def __init__(self, redis_client):
        self.redis = redis_client

    async def issue(self, user, label, issued_by, expires_in=None):
        raw_key = _generate_raw_key()
        key_id = secrets.token_hex(8)
        now = time.time()
        meta = {
            "id": key_id,
            "user": user,
            "label": label or "",
            "created_at": now,
            "created_by": issued_by,
            "last_used_at": "",
            "revoked": "0",
            "expires_at": str(now + expires_in) if expires_in else "",
        }
        await self.redis.hset(KEY_META_KEY + key_id, mapping=meta)
        await self.redis.set(KEY_HASH_KEY + _hash(raw_key), key_id)
        await self.redis.sadd(USER_KEYS_KEY + user, key_id)
        await self.redis.sadd(ALL_KEYS_SET, key_id)
        return raw_key, meta

    async def revoke_auto_keys(self, user):
        """Revoke every existing auto-provisioned container key for this
        user. Call this right before minting a fresh one for a genuinely new
        container, so a rebuilt container doesn't leave its predecessor's
        key sitting around as an unused, never-revoked orphan."""
        for meta in await self.list_for_user(user):
            if meta.get("label") == AUTO_PROVISIONED_LABEL and meta.get("revoked") != "1":
                await self.revoke(meta["id"])

    async def verify(self, raw_key):
        """Return the owning username for a valid, unrevoked, unexpired
        key, or None. Updates last_used_at as a side effect on success."""
        if not raw_key:
            return None
        key_id = await self.redis.get(KEY_HASH_KEY + _hash(raw_key))
        if not key_id:
            return None
        meta = await self.redis.hgetall(KEY_META_KEY + key_id)
        if not meta or meta.get("revoked") == "1":
            return None
        expires_at = meta.get("expires_at")
        if expires_at and float(expires_at) < time.time():
            return None
        await self.redis.hset(KEY_META_KEY + key_id, "last_used_at", time.time())
        return meta["user"]

    async def revoke(self, key_id):
        exists = await self.redis.exists(KEY_META_KEY + key_id)
        if not exists:
            return False
        await self.redis.hset(KEY_META_KEY + key_id, "revoked", "1")
        return True

    async def get(self, key_id):
        meta = await self.redis.hgetall(KEY_META_KEY + key_id)
        return meta or None

    async def list_for_user(self, user):
        key_ids = await self.redis.smembers(USER_KEYS_KEY + user)
        return await self._hydrate(key_ids)

    async def list_all(self):
        key_ids = await self.redis.smembers(ALL_KEYS_SET)
        return await self._hydrate(key_ids)

    async def _hydrate(self, key_ids):
        metas = []
        for key_id in key_ids:
            meta = await self.redis.hgetall(KEY_META_KEY + key_id)
            if meta:
                metas.append(meta)
        metas.sort(key=lambda m: float(m.get("created_at", 0)))
        return metas
