"""Local accounts: scrypt password hashing, server-side sessions, cookie handling.

- Passwords use hashlib.scrypt (memory-hard, stdlib). Never stored or logged in plain text.
- Session cookie is random; only its SHA-256 is stored. HttpOnly, SameSite=Lax, Secure over https.
- Sessions expire after SESSION_DAYS and are deleted on logout.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .db import get_db, utcnow
from .models import User, UserSession

SESSION_COOKIE = "wp_session"
SESSION_DAYS = 30
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,253}\.[^@\s]{2,}$")
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1
_DUMMY_HASH = None  # built lazily; used to keep login timing uniform for unknown emails


def normalize_email(value: str) -> str:
    return value.strip().lower()


def validate_email(value: str) -> str:
    email = normalize_email(value)
    if len(email) > 254 or not _EMAIL_RE.match(email):
        raise ValueError("enter a valid email address")
    return email


def validate_password(value: str) -> str:
    if len(value) < 10 or len(value) > 128:
        raise ValueError("password must be 10 to 128 characters")
    if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
        raise ValueError("password must contain a letter and a digit")
    return value


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                                   n=int(n), r=int(r), p=int(p), dklen=len(digest_hex) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate.hex(), digest_hex)


def dummy_verify(password: str) -> None:
    """Spend comparable time on unknown emails so responses do not reveal which emails exist."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("dummy-password-1")
    verify_password(password, _DUMMY_HASH)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_session(db: Session, user: User, response: Response, secure: bool) -> None:
    raw = secrets.token_urlsafe(32)
    now = utcnow()
    db.add(UserSession(user_id=user.id, token_hash=hash_token(raw), created_at=now,
                       expires_at=now + timedelta(days=SESSION_DAYS)))
    user.last_login_at = now
    db.commit()
    response.set_cookie(SESSION_COOKIE, raw, max_age=SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=secure, path="/")


def clear_session(db: Session, request: Request, response: Response) -> None:
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        db.execute(delete(UserSession).where(UserSession.token_hash == hash_token(raw)))
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    row = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(raw)))
    if row is None or row.expires_at < utcnow():
        return None
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    return user


def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="sign in required")
    return user
