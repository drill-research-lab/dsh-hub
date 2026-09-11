"""Per-user CPU/memory limits (design doc section 3), shared by Hub and Panel.

Redis is the source of truth so a limit an admin sets is available
immediately both to a future spawn (Hub reads it via DockerSpawner's
mem_limit/cpu_limit callables) and to an already-running container (Panel
applies it live with a `docker update`, through the same docker-socket-proxy
Hub already uses to spawn containers). A user with no entry runs under the
process-wide defaults, not unlimited.
"""
import time

LIMIT_KEY_PREFIX = "dispatcher:resource-limits:"  # + user -> {cpu, memory_mb, ...} hash
ALL_LIMITS_KEY = "dispatcher:resource-limits:all"  # set of usernames with a non-default limit

DEFAULT_CPU_CORES = 2.0
DEFAULT_MEMORY_MB = 4096

MIN_CPU_CORES = 0.25
MAX_CPU_CORES = 8.0
MIN_MEMORY_MB = 512
MAX_MEMORY_MB = 16384


class ResourceLimitStore:
    def __init__(self, redis_client, default_cpu=DEFAULT_CPU_CORES, default_memory_mb=DEFAULT_MEMORY_MB):
        self.redis = redis_client
        self.default_cpu = default_cpu
        self.default_memory_mb = default_memory_mb

    async def get(self, user):
        """Return this user's effective limit, falling back to the
        process-wide default (marked with is_default=True) if they've never
        had a custom one set."""
        data = await self.redis.hgetall(LIMIT_KEY_PREFIX + user)
        if not data:
            return {
                "user": user,
                "cpu": self.default_cpu,
                "memory_mb": self.default_memory_mb,
                "is_default": True,
            }
        return {
            "user": user,
            "cpu": float(data["cpu"]),
            "memory_mb": int(data["memory_mb"]),
            "is_default": False,
            "updated_at": data.get("updated_at"),
            "updated_by": data.get("updated_by"),
        }

    def validate(self, cpu, memory_mb):
        if not (MIN_CPU_CORES <= cpu <= MAX_CPU_CORES):
            raise ValueError(f"cpu must be between {MIN_CPU_CORES} and {MAX_CPU_CORES} cores")
        if not (MIN_MEMORY_MB <= memory_mb <= MAX_MEMORY_MB):
            raise ValueError(f"memory_mb must be between {MIN_MEMORY_MB} and {MAX_MEMORY_MB}")

    async def set(self, user, cpu, memory_mb, actor):
        self.validate(cpu, memory_mb)
        await self.redis.hset(
            LIMIT_KEY_PREFIX + user,
            mapping={
                "cpu": cpu,
                "memory_mb": memory_mb,
                "updated_at": time.time(),
                "updated_by": actor,
            },
        )
        await self.redis.sadd(ALL_LIMITS_KEY, user)
        return await self.get(user)

    async def list_all(self):
        users = await self.redis.smembers(ALL_LIMITS_KEY)
        return [await self.get(user) for user in sorted(users)]


def docker_update_body(cpu, memory_mb):
    """Build the JSON body for `POST /containers/{name}/update`. CpuPeriod
    is fixed at Docker's own default (100ms) and CpuQuota scales with it, per
    Docker's documented CFS-quota mechanism. MemorySwap is pinned equal to
    Memory to disable swap rather than leaving it at the container's
    existing (possibly unlimited) swap ceiling -- Docker rejects a Memory
    update that would leave Memory > MemorySwap.
    """
    cpu_period = 100000
    memory_bytes = memory_mb * 1024 * 1024
    return {
        "CpuPeriod": cpu_period,
        "CpuQuota": int(cpu * cpu_period),
        "Memory": memory_bytes,
        "MemorySwap": memory_bytes,
    }
