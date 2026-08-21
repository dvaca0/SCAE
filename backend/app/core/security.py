"""Password hashing, token issuing and CSRF helpers.

Choices worth stating:
  * Argon2id for passwords. The prototype used Werkzeug's default PBKDF2;
    Argon2id is the current OWASP recommendation and is memory-hard.
  * The session token is a signed JWT delivered in an httpOnly cookie, so
    page scripts cannot read it (XSS cannot exfiltrate a session).
  * Because a cookie is sent automatically, mutations additionally require
    a double-submit CSRF token that JS *can* read and echo in a header.
"""
from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import get_settings

settings = get_settings()
_hasher = PasswordHasher()

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, InvalidHashError, Exception):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except Exception:
        return True


def create_access_token(subject: str, role: str, extra: Optional[dict[str, Any]] = None) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_minutes),
        "iss": "scae",
        # Distinguishes one login from another, so a single session can be
        # revoked later without invalidating the signing key.
        "jti": secrets.token_urlsafe(12),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[dict[str, Any]]:
    try:
        return jwt.decode(
            token, settings.secret_key, algorithms=[ALGORITHM], issuer="scae"
        )
    except jwt.PyJWTError:
        return None


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_tokens_match(cookie_value: str | None, header_value: str | None) -> bool:
    if not cookie_value or not header_value:
        return False
    return hmac.compare_digest(cookie_value, header_value)
