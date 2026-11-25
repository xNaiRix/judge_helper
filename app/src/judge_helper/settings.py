"""Конфигурация приложения (env-переменные, значения по умолчанию для dev).

Изменение Google OAuth и других флагов выполняется конфигурацией (env),
без релиза приложения — как требует ТЗ.
"""

from __future__ import annotations

import os


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    # --- Безопасность / JWT ---
    SECRET_KEY: str = os.environ.get("APP_SECRET_KEY", "dev-secret-поменяйте-в-продакшене")
    ACCESS_TOKEN_TTL_MIN: int = int(os.environ.get("ACCESS_TOKEN_TTL_MIN", "480"))  # 8 часов
    REFRESH_TOKEN_TTL_DAYS: int = int(os.environ.get("REFRESH_TOKEN_TTL_DAYS", "30"))

    # --- Feature flag: Google OAuth ---
    GOOGLE_OAUTH_ENABLED: bool = _flag("GOOGLE_OAUTH_ENABLED")
    GOOGLE_CLIENT_ID: str = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_REDIRECT_URI: str = os.environ.get("GOOGLE_REDIRECT_URI", "")

    # --- Демо-режим: эхо токенов из «писем» (только dev; в prod выключить) ---
    ECHO_EMAIL_TOKENS: bool = _flag("ECHO_EMAIL_TOKENS", default="1")

    # --- Rate limiting ---
    RATE_LIMIT_PER_MIN: int = int(os.environ.get("RATE_LIMIT_PER_MIN", "100"))
    LOGIN_MAX_ATTEMPTS: int = 5  # неудачных попыток входа…
    LOGIN_WINDOW_MIN: int = 15  # …за 15 минут (ТЗ)

    # --- Сроки (ТЗ) ---
    INVITE_TTL_DAYS: int = 7  # приглашение — 1 неделя
    RESET_TTL_HOURS: int = 24  # ссылка сброса пароля — 24 часа
    AUDIT_RETENTION_DAYS: int = 180  # аудит-лог — 6 месяцев
    COMPETITION_AUTODELETE_DAYS: int = 365  # автоудаление через год после завершения

    # --- Логи ---
    DEBUG: bool = _flag("APP_DEBUG")


settings = Settings()
