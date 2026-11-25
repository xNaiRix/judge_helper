"""Авторизация: регистрация с подтверждением email, JWT-вход, refresh/logout,
восстановление пароля (24 ч, завершает все сессии), Google OAuth (feature flag),
ограничение неудачных попыток входа (5 за 15 минут)."""

from __future__ import annotations

from datetime import timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import get_current_user, get_db
from ..middleware import LoginThrottler
from ..models import AuthToken, RevokedToken, User, utcnow
from ..schemas import (
    ConfirmEmailIn,
    ForgotPasswordIn,
    ForgotPasswordOut,
    GoogleUrlOut,
    LoginIn,
    LoginOut,
    LogoutIn,
    RefreshIn,
    RefreshOut,
    RegisterIn,
    RegisterOut,
    ResetPasswordIn,
    UserOut,
)
from ..security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    random_token,
    verify_password,
)
from ..services import audit, send_email
from ..settings import settings

router = APIRouter(prefix="/auth", tags=["Авторизация"])


def _issue_tokens(user: User) -> dict:
    return {
        "access_token": create_access_token(
            user.id, user.token_version, settings.SECRET_KEY, settings.ACCESS_TOKEN_TTL_MIN
        ),
        "refresh_token": create_refresh_token(
            user.id, user.token_version, settings.SECRET_KEY, settings.REFRESH_TOKEN_TTL_DAYS
        ),
    }


def _get_user_by_email(db: Session, email: str) -> User | None:
    return db.execute(select(User).where(User.email == email.strip().lower())).scalar_one_or_none()


# ------------------------------ Регистрация --------------------------------


@router.post(
    "/register",
    response_model=RegisterOut,
    status_code=201,
    summary="Регистрация судьи (с подтверждением email)",
)
def register(data: RegisterIn, request: Request, db: Session = Depends(get_db)):
    if _get_user_by_email(db, data.email) is not None:
        raise HTTPException(
            status_code=409,
            detail="Пользователь с таким email уже зарегистрирован",
        )

    user = User(
        email=data.email,
        full_name=data.full_name.strip(),
        password_hash=hash_password(data.password),
        email_confirmed=False,
    )
    db.add(user)
    db.flush()

    # Письмо с ссылкой подтверждения (срок — 24 часа)
    token = random_token()
    db.add(
        AuthToken(
            user_id=user.id,
            type="confirm",
            token=token,
            expires_at=utcnow() + timedelta(hours=24),
        )
    )
    send_email(
        db,
        user.email,
        "Подтверждение email",
        f"Для подтверждения email перейдите по ссылке и введите токен: {token}",
    )
    audit(db, request, user, "judge", "create", "user", user.id, new_value={"email": user.email})
    db.commit()
    db.refresh(user)

    return RegisterOut(
        user=UserOut.model_validate(user),
        # Только в демо-режиме: иначе токен доступен лишь из письма
        confirmation_token=token if settings.ECHO_EMAIL_TOKENS else None,
    )


@router.post("/confirm-email", summary="Подтверждение email по токену из письма")
def confirm_email(data: ConfirmEmailIn, db: Session = Depends(get_db)):
    auth_token = db.execute(
        select(AuthToken).where(AuthToken.token == data.token, AuthToken.type == "confirm")
    ).scalar_one_or_none()
    if auth_token is None or auth_token.used:
        raise HTTPException(status_code=400, detail="Неверный токен подтверждения")
    if auth_token.expires_at < utcnow():
        raise HTTPException(status_code=410, detail="Срок действия ссылки истёк")

    user = db.get(User, auth_token.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="Пользователь не найден")
    user.email_confirmed = True
    auth_token.used = True
    db.commit()
    return {"status": "confirmed"}


# ---------------------------------- Вход -----------------------------------


@router.post("/login", response_model=LoginOut, summary="Вход (после подтверждения email)")
def login(data: LoginIn, request: Request, db: Session = Depends(get_db)):
    throttler: LoginThrottler = request.app.state.login_throttler
    ip = request.client.host if request.client else "unknown"
    key = f"{data.email.strip().lower()}|{ip}"

    # Не более 5 неудачных попыток за 15 минут (ТЗ)
    if throttler.blocked(key):
        raise HTTPException(
            status_code=429,
            detail="Слишком много неудачных попыток входа. Повторите через 15 минут",
        )

    user = _get_user_by_email(db, data.email)
    if user is None or not verify_password(data.password, user.password_hash):
        throttler.register_failure(key)
        raise HTTPException(status_code=401, detail="Неверный email или пароль")

    if not user.email_confirmed:
        raise HTTPException(
            status_code=403,
            detail="Email не подтверждён — проверьте письмо и подтвердите регистрацию",
        )

    return LoginOut(user=UserOut.model_validate(user), **_issue_tokens(user))


# ------------------------------ Refresh / logout ----------------------------


@router.post("/refresh", response_model=RefreshOut, summary="Обновление access-токена")
def refresh(data: RefreshIn, request: Request, db: Session = Depends(get_db)):
    try:
        payload = decode_token(data.refresh_token, settings.SECRET_KEY, expected_type="refresh")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Неверный или просроченный refresh-токен")

    if db.query(RevokedToken).filter(RevokedToken.jti == payload.get("jti", "")).first():
        raise HTTPException(status_code=401, detail="Refresh-токен отозван")

    user = db.get(User, int(payload["sub"]))
    if user is None or user.token_version != payload.get("ver", -1):
        raise HTTPException(status_code=401, detail="Сессия завершена — войдите заново")

    return RefreshOut(**_issue_tokens(user))


@router.post("/logout", status_code=204, summary="Выход (отзыв токенов)")
def logout(
    data: LogoutIn, request: Request, user=Depends(get_current_user), db: Session = Depends(get_db)
):
    auth_header = request.headers.get("authorization", "")
    access = auth_header[len("Bearer ") :].strip() if auth_header.startswith("Bearer ") else ""
    try:
        payload = decode_token(access, settings.SECRET_KEY, expected_type="access")
        db.add(
            RevokedToken(
                jti=payload["jti"],
                expires_at=utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_TTL_MIN),
            )
        )
    except jwt.PyJWTError:
        pass
    if data.refresh_token:
        try:
            rp = decode_token(data.refresh_token, settings.SECRET_KEY, expected_type="refresh")
            db.add(
                RevokedToken(
                    jti=rp["jti"],
                    expires_at=utcnow() + timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS),
                )
            )
        except jwt.PyJWTError:
            pass
    db.commit()


# --------------------------- Восстановление пароля -------------------------


@router.post(
    "/forgot-password",
    response_model=ForgotPasswordOut,
    summary="Запрос ссылки сброса пароля (срок 24 часа)",
)
def forgot_password(data: ForgotPasswordIn, request: Request, db: Session = Depends(get_db)):
    user = _get_user_by_email(db, data.email)
    # Не раскрываем существование аккаунта — одинаковый ответ в любом случае
    reset_token = None
    if user is not None:
        reset_token = random_token()
        db.add(
            AuthToken(
                user_id=user.id,
                type="reset",
                token=reset_token,
                expires_at=utcnow() + timedelta(hours=settings.RESET_TTL_HOURS),
            )
        )
        send_email(
            db,
            user.email,
            "Сброс пароля",
            f"Ссылка действует 24 часа. Токен сброса: {reset_token}",
        )
        audit(
            db,
            request,
            user,
            "judge",
            "update",
            "user",
            user.id,
            new_value={"action": "password_reset_requested"},
        )
        db.commit()
    return ForgotPasswordOut(
        reset_token=reset_token if settings.ECHO_EMAIL_TOKENS else None,
    )


@router.post("/reset-password", summary="Сброс пароля по токену (завершает все сессии)")
def reset_password(data: ResetPasswordIn, request: Request, db: Session = Depends(get_db)):
    auth_token = db.execute(
        select(AuthToken).where(AuthToken.token == data.token, AuthToken.type == "reset")
    ).scalar_one_or_none()
    if auth_token is None or auth_token.used:
        raise HTTPException(status_code=400, detail="Неверный токен сброса")
    if auth_token.expires_at < utcnow():
        raise HTTPException(status_code=410, detail="Срок действия ссылки истёк")

    user = db.get(User, auth_token.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="Пользователь не найден")

    user.password_hash = hash_password(data.new_password)
    # Все активные сессии завершаются (ТЗ)
    user.token_version += 1
    auth_token.used = True
    audit(
        db,
        request,
        user,
        "judge",
        "update",
        "user",
        user.id,
        new_value={"action": "password_reset"},
    )
    db.commit()
    return {"status": "password_changed", "sessions_terminated": True}


# ------------------------------ Google OAuth --------------------------------


@router.get(
    "/google/url",
    response_model=GoogleUrlOut,
    summary="Ссылка Google OAuth (feature flag: GOOGLE_OAUTH_ENABLED)",
)
def google_url():
    # Флаг выключен — эндпоинт недоступен (ТЗ: 404/403 без релиза приложения)
    if not settings.GOOGLE_OAUTH_ENABLED:
        raise HTTPException(status_code=404, detail="Google OAuth отключён конфигурацией")
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=501,
            detail="Google OAuth включён, но GOOGLE_CLIENT_ID не настроен",
        )
    redirect = settings.GOOGLE_REDIRECT_URI or "urn:ietf:wg:oauth:2.0:oob"
    url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.GOOGLE_CLIENT_ID}"
        "&response_type=code"
        "&scope=openid%20email%20profile"
        f"&redirect_uri={redirect}"
    )
    return GoogleUrlOut(url=url, message="Передайте код авторизации в /auth/google/callback")


@router.post("/google/callback", summary="Обмен кода Google OAuth (требует настройки клиента)")
def google_callback():
    if not settings.GOOGLE_OAUTH_ENABLED:
        raise HTTPException(status_code=404, detail="Google OAuth отключён конфигурацией")
    raise HTTPException(
        status_code=501,
        detail="Обмен кода требует настроенного Google-клиента и секретов на сервере",
    )


# ---------------------------------- Профиль ---------------------------------


@router.get("/me", response_model=UserOut, summary="Текущий пользователь")
def me(user=Depends(get_current_user)):
    return UserOut.model_validate(user)
