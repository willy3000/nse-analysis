from __future__ import annotations

import hmac
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from nse_platform.auth.service import resolve_session
from nse_platform.core.db import get_sessionmaker
from nse_platform.models import User

SESSION_COOKIE = "nse_session"
CSRF_COOKIE = "nse_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_db() -> Iterator[Session]:
    db = get_sessionmaker()()
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def client_ip(request: Request) -> str | None:
    # Behind a trusted reverse proxy uvicorn's --proxy-headers sets request.client from X-Forwarded-For.
    return request.client.host if request.client else None


def csrf_protect(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise HTTPException(status_code=403, detail={"code": "CSRF_FAILED", "message": "Missing or invalid CSRF token"})


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    user = resolve_session(db, token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHENTICATED", "message": "Sign in required"})
    csrf_protect(request)
    request.state.user_id = user.id
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Administrator role required"})
    return user


def not_found(what: str = "Resource") -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": f"{what} not found"})
