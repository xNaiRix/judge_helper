"""Безопасность: пароли и JWT.

* Пароли — PBKDF2-HMAC-SHA256 со случайной солью (не хранятся в открытом виде).
* Токены — JWT (HS256): проверяются подпись, срок действия и алгоритм.
  Access-токены короткоживущие; refresh-токены — длительные, ротируются при logout.
  Актуальность всех токенов пользователя управляется версией token_version
  (сброс пароля завершает все сессии).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta
from typing import Any

import jwt

from .models import utcnow

ITERATIONS = 100_000
ALGORITHM = "HS256"


# ----------------------------- Пароли -----------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), ITERATIONS).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), ITERATIONS).hex()
    return secrets.compare_digest(candidate, digest)


# ------------------------------ JWT -------------------------------


def create_access_token(user_id: int, token_version: int, secret: str, ttl_minutes: int) -> str:
    now = utcnow()
    payload = {
        "sub": str(user_id),
        "ver": token_version,
        "type": "access",
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def create_refresh_token(user_id: int, token_version: int, secret: str, ttl_days: int) -> str:
    now = utcnow()
    payload = {
        "sub": str(user_id),
        "ver": token_version,
        "type": "refresh",
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=ttl_days)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_token(token: str, secret: str, expected_type: str) -> dict[str, Any]:
    """Декодирует и проверяет токен: подпись, алгоритм, срок, тип.

    Бросает jwt.PyJWTError при любой проблеме.
    """
    payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("Неверный тип токена")
    return payload


def random_token() -> str:
    """Одноразовый токен для подтверждения email / сброса пароля."""
    return secrets.token_urlsafe(32)
