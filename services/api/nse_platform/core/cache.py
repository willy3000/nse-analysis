"""Optional Redis cache. If Redis is not configured or unreachable, every call degrades to a cache miss."""

from __future__ import annotations

import json
import logging
import time
from functools import lru_cache
from typing import Any

from nse_platform.core.config import get_settings

log = logging.getLogger(__name__)


class Cache:
    def __init__(self, url: str | None):
        self._client = None
        self._down_until = 0.0
        self.last_error: str | None = None
        if url:
            import redis

            self._client = redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)

    @property
    def configured(self) -> bool:
        return self._client is not None

    def _ok(self) -> bool:
        return self._client is not None and time.monotonic() >= self._down_until

    def _fail(self, exc: Exception) -> None:
        self.last_error = f"{type(exc).__name__}: {exc}"
        self._down_until = time.monotonic() + 30  # circuit breaker: retry Redis after 30 s
        log.warning("redis_unavailable", extra={"error": self.last_error})

    def get_json(self, key: str) -> Any | None:
        if not self._ok():
            return None
        try:
            raw = self._client.get(key)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 - degrade to miss, recorded in last_error + log
            self._fail(exc)
            return None
        return json.loads(raw) if raw else None

    def set_json(self, key: str, value: Any, ttl: int) -> None:
        if not self._ok():
            return
        try:
            self._client.setex(key, ttl, json.dumps(value, default=str))  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)

    def incr_window(self, key: str, window: int) -> int | None:
        if not self._ok():
            return None
        try:
            pipe = self._client.pipeline()  # type: ignore[union-attr]
            pipe.incr(key)
            pipe.expire(key, window)
            return int(pipe.execute()[0])
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            return None

    def ping(self) -> bool | None:
        if self._client is None:
            return None
        try:
            return bool(self._client.ping())
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            return False


@lru_cache
def get_cache() -> Cache:
    return Cache(get_settings().redis_url)
