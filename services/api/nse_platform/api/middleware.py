"""Request ID, security headers, rate limiting and the consistent error envelope."""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from nse_platform.core.cache import get_cache
from nse_platform.core.config import get_settings
from nse_platform.core.logging import request_id_var

log = logging.getLogger("nse_platform.http")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = request_id_var.set(rid[:64])
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        dur = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = rid[:64]
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
        if get_settings().cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path != "/health/live":
            log.info("http_request", extra={"method": request.method, "path": request.url.path,
                                            "status": response.status_code, "duration_ms": round(dur, 1),
                                            "request_id": rid[:64]})
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed one-minute windows per client IP and route class. Redis-backed when available, else in-process."""

    def __init__(self, app: FastAPI) -> None:
        super().__init__(app)
        self._local: dict[str, int] = defaultdict(int)
        self._window = 0

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        s = get_settings()
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        is_login = path.endswith("/auth/login") or path.endswith("/auth/register")
        limit = s.login_rate_limit_per_minute if is_login else s.rate_limit_per_minute
        ip = request.client.host if request.client else "unknown"
        window = int(time.time() // 60)
        key = f"rl:{'login' if is_login else 'api'}:{ip}:{window}"
        count = get_cache().incr_window(key, 61)
        if count is None:
            if window != self._window:
                self._local.clear()
                self._window = window
            self._local[key] += 1
            count = self._local[key]
        if count > limit:
            return JSONResponse(status_code=429, headers={"Retry-After": str(60 - int(time.time()) % 60)},
                                content={"error": {"code": "RATE_LIMITED", "message": "Too many requests"}})
        return await call_next(request)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            body = {"error": detail}
        else:
            body = {"error": {"code": {400: "BAD_REQUEST", 401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND",
                                       409: "CONFLICT"}.get(exc.status_code, "ERROR"), "message": str(detail)}}
        return JSONResponse(status_code=exc.status_code, content=body, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"error": {"code": "VALIDATION_ERROR",
                                                                "message": "Request validation failed",
                                                                "details": errors}})

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", extra={"path": request.url.path})
        return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL_ERROR",
                                                                "message": "Internal server error",
                                                                "request_id": request_id_var.get()}})
