from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from nse_platform.api.deps import CSRF_COOKIE, SESSION_COOKIE, client_ip, csrf_protect, current_user, get_db
from nse_platform.auth.service import AuthError, authenticate, create_session, create_user, revoke_session
from nse_platform.core.config import get_settings
from nse_platform.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class Credentials(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=1, max_length=256)


class Registration(Credentials):
    display_name: str | None = Field(None, max_length=128)


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str | None
    role: str


def _set_cookies(response: Response, token: str | None) -> None:
    s = get_settings()
    if token:
        response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=s.cookie_secure, samesite="lax",
                            max_age=s.session_absolute_hours * 3600, path="/")
    response.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(24), httponly=False, secure=s.cookie_secure,
                        samesite="lax", max_age=s.session_absolute_hours * 3600, path="/")


def _out(u: User) -> UserOut:
    return UserOut(id=u.id, email=u.email, display_name=u.display_name, role=u.role)


@router.get("/csrf", summary="Issue a CSRF cookie (call once before the first POST)")
def csrf(response: Response) -> dict[str, bool]:
    _set_cookies(response, None)
    return {"ok": True}


@router.post("/register", response_model=UserOut, dependencies=[Depends(csrf_protect)])
def register(body: Registration, request: Request, response: Response, db: Session = Depends(get_db)) -> UserOut:
    if not get_settings().allow_registration:
        raise HTTPException(403, detail={"code": "REGISTRATION_DISABLED", "message": "Registration is disabled"})
    try:
        u = create_user(db, body.email, body.password, body.display_name)
    except AuthError as exc:
        raise HTTPException(400, detail={"code": "REGISTRATION_FAILED", "message": str(exc)}) from exc
    _set_cookies(response, create_session(db, u, client_ip(request), request.headers.get("user-agent")))
    return _out(u)


@router.post("/login", response_model=UserOut, dependencies=[Depends(csrf_protect)])
def login(body: Credentials, request: Request, response: Response, db: Session = Depends(get_db)) -> UserOut:
    try:
        u = authenticate(db, body.email, body.password, client_ip(request))
    except AuthError as exc:
        db.commit()  # persist failed-attempt counters / audit rows before raising
        raise HTTPException(401, detail={"code": "LOGIN_FAILED", "message": str(exc)}) from exc
    _set_cookies(response, create_session(db, u, client_ip(request), request.headers.get("user-agent")))
    return _out(u)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db),
           _: User = Depends(current_user)) -> dict[str, bool]:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        revoke_session(db, token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> UserOut:
    return _out(user)
