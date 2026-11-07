"""Password hashing (Argon2id), session tokens (only SHA-256 hashes stored), lockout, audit logging (ADR-0005)."""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nse_platform.core.config import get_settings
from nse_platform.models import AuditLog, User, UserSession

_ph = PasswordHasher()
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_FAILED = 5
LOCK_MINUTES = 15


class AuthError(Exception):
    pass


def validate_password(pw: str) -> None:
    if len(pw) < 10:
        raise AuthError("Password must be at least 10 characters")
    if pw.lower() == pw or pw.upper() == pw or not any(ch.isdigit() for ch in pw):
        raise AuthError("Password must mix upper- and lower-case letters and include a digit")


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(hashed: str, pw: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except (VerificationError, InvalidHashError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def audit(db: Session, action: str, user_id: int | None, ip: str | None = None, entity: str | None = None,
          entity_id: str | None = None, **details: object) -> None:
    db.add(AuditLog(user_id=user_id, action=action, ip=ip, entity=entity, entity_id=entity_id,
                    details=details or None))


def create_user(db: Session, email: str, password: str, display_name: str | None = None, role: str = "user") -> User:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise AuthError("Invalid email address")
    validate_password(password)
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise AuthError("An account with this email already exists")
    u = User(email=email, password_hash=hash_password(password), display_name=display_name, role=role)
    db.add(u)
    db.flush()
    audit(db, "user.create", u.id, entity="user", entity_id=str(u.id), role=role)
    return u


_DUMMY_HASH = _ph.hash("timing-equalisation-dummy-password")


def authenticate(db: Session, email: str, password: str, ip: str | None) -> User:
    u = db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))
    now = datetime.now(timezone.utc)
    if u is None:
        verify_password(_DUMMY_HASH, password)  # equalise timing so user existence is not revealed
        audit(db, "auth.login_failed", None, ip, reason="unknown_email")
        raise AuthError("Invalid email or password")
    if u.locked_until and u.locked_until > now:
        audit(db, "auth.login_locked", u.id, ip)
        raise AuthError("Account temporarily locked after repeated failures. Try again later.")
    if not u.is_active or not verify_password(u.password_hash, password):
        u.failed_logins = (u.failed_logins or 0) + 1
        if u.failed_logins >= MAX_FAILED:
            u.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            u.failed_logins = 0
        audit(db, "auth.login_failed", u.id, ip, reason="bad_password_or_inactive")
        raise AuthError("Invalid email or password")
    if _ph.check_needs_rehash(u.password_hash):
        u.password_hash = hash_password(password)
    u.failed_logins = 0
    u.locked_until = None
    u.last_login_at = now
    audit(db, "auth.login", u.id, ip)
    return u


def create_session(db: Session, user: User, ip: str | None, user_agent: str | None) -> str:
    s = get_settings()
    token = secrets.token_urlsafe(32)
    db.add(UserSession(user_id=user.id, token_hash=token_hash(token), ip=ip, user_agent=(user_agent or "")[:512],
                       expires_at=datetime.now(timezone.utc) + timedelta(hours=s.session_absolute_hours)))
    return token


def resolve_session(db: Session, token: str) -> User | None:
    s = get_settings()
    now = datetime.now(timezone.utc)
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == token_hash(token)))
    if sess is None or sess.revoked_at is not None or sess.expires_at <= now:
        return None
    if sess.last_seen_at < now - timedelta(minutes=s.session_idle_minutes):
        sess.revoked_at = now
        return None
    if sess.last_seen_at < now - timedelta(minutes=1):
        sess.last_seen_at = now
    user = sess.user
    return user if user.is_active else None


def revoke_session(db: Session, token: str) -> None:
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == token_hash(token)))
    if sess and sess.revoked_at is None:
        sess.revoked_at = datetime.now(timezone.utc)
        audit(db, "auth.logout", sess.user_id)
