"""Read-only resilient HTTP client used by provider adapters.

- GET only: refuses any other method, so an adapter can never place orders even by mistake.
- Exponential backoff with full jitter on network errors, 5xx and 429 (honours Retry-After).
- Captures X-RateLimit-* headers for the Data Centre.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from nse_platform.providers.base import (
    ProviderAuthError,
    ProviderBadResponse,
    ProviderRateLimited,
    ProviderUnavailable,
    RateLimitInfo,
)

log = logging.getLogger(__name__)


class ReadOnlyHttpClient:
    def __init__(
        self,
        base_url: str,
        headers: dict[str, str],
        timeout: float = 20.0,
        max_retries: int = 4,
        backoff_base: float = 1.0,
        backoff_cap: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Any = asyncio.sleep,
        min_interval: float = 0.0,
    ):
        self._client = httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=timeout, transport=transport, follow_redirects=False
        )
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.rate_limit = RateLimitInfo()
        self._sleep = sleep
        self.min_interval = min_interval  # proactive pacing to stay under the provider's per-minute limit
        self._last_request = 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

    def _capture_rate_limit(self, resp: httpx.Response) -> None:
        def _int(name: str) -> int | None:
            v = resp.headers.get(name)
            try:
                return int(v) if v is not None else None
            except ValueError:
                return None

        limit, remaining, reset = _int("X-RateLimit-Limit"), _int("X-RateLimit-Remaining"), _int("X-RateLimit-Reset")
        if limit is not None:
            self.rate_limit.limit = limit
        if remaining is not None:
            self.rate_limit.remaining = remaining
        if reset is not None:
            self.rate_limit.reset_at = datetime.fromtimestamp(reset, tz=timezone.utc)

    def _delay(self, attempt: int) -> float:
        return random.uniform(0, min(self.backoff_cap, self.backoff_base * (2**attempt)))

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if self.min_interval:
                wait = self._last_request + self.min_interval - time.monotonic()
                if wait > 0:
                    await self._sleep(wait)
                self._last_request = time.monotonic()
            try:
                resp = await self._client.request("GET", path, params=clean)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = ProviderUnavailable(f"network error on GET {path}: {exc!r}")
                log.warning("provider_network_error", extra={"path": path, "attempt": attempt, "error": repr(exc)})
            else:
                self._capture_rate_limit(resp)
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise ProviderBadResponse(f"non-JSON response from GET {path}") from exc
                if resp.status_code in (401, 403):
                    raise ProviderAuthError(f"GET {path} -> {resp.status_code}: {resp.text[:300]}")
                if resp.status_code == 404:
                    raise ProviderBadResponse(f"GET {path} -> 404: {resp.text[:300]}")
                if resp.status_code == 429:
                    ra = resp.headers.get("Retry-After")
                    retry_after = float(ra) if ra and ra.replace(".", "", 1).isdigit() else None
                    last_exc = ProviderRateLimited(f"rate limited on GET {path}", retry_after)
                    if attempt < self.max_retries:
                        await self._sleep(retry_after if retry_after is not None else self._delay(attempt))
                        continue
                    raise last_exc
                if resp.status_code >= 500:
                    last_exc = ProviderUnavailable(f"GET {path} -> {resp.status_code}")
                else:
                    raise ProviderBadResponse(f"GET {path} -> {resp.status_code}: {resp.text[:300]}")
            if attempt < self.max_retries:
                await self._sleep(self._delay(attempt))
        assert last_exc is not None
        raise last_exc

    async def request(self, method: str, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - guard
        raise PermissionError("Provider HTTP client is read-only; only GET requests are permitted")
