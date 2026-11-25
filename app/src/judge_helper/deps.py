"""Зависимости FastAPI: сессия БД и текущий пользователь (JWT)."""

from __future__ import annotations

import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from .models import RevokedToken, User
from .security import decode_token
from .settings import settings


def get_db(request: Request):
    db: Session = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    request: Request,
    authorization: str = Header(default="", description="Bearer <access-токен>"),
    db: Session = Depends(get_db),
) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Требуется заголовок Authorization: Bearer <токен>",
        )
    token = authorization[len("Bearer ") :].strip()
    try:
        payload = decode_token(token, settings.SECRET_KEY, expected_type="access")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Неверный или просроченный токен")

    # logout: проверка отзыва jti
    jti = payload.get("jti", "")
    revoked = db.query(RevokedToken).filter(RevokedToken.jti == jti).first()
    if revoked is not None:
        raise HTTPException(status_code=401, detail="Токен отозван (logout)")

    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    # сброс пароля завершает все сессии (проверка версии токенов)
    if user.token_version != payload.get("ver", -1):
        raise HTTPException(status_code=401, detail="Сессия завершена — войдите заново")
    return user
