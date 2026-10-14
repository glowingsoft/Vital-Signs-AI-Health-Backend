# One shared async Redis connection for the whole process.
# Created the first time it is needed, closed when the app or worker shuts down.

from typing import Optional

import redis.asyncio as redis

from app import config

_client: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(
            config.REDIS_URL,
            decode_responses=True,  # return str instead of bytes
            socket_connect_timeout=1,  # if Redis is down, fail after 1 s instead of making the user wait
            # Max wait for any answer. Must be longer than the worker's XREADGROUP wait (BLOCK_MS = 5 s),
            # otherwise an empty wait would end as a timeout error.
            socket_timeout=10,
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def ping() -> bool:
    """True if Redis answers, False if it is down. Never raises."""
    try:
        return await get_redis().ping()
    except Exception:
        return False
