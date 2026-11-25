"""Фабрика FastAPI-приложения «Помощник судьи»."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .database import Base, make_engine, make_session_factory, run_simple_migrations
from .middleware import LoginThrottler, RateLimiter, register_middleware
from .routers import auth, competitions, participants, reports, results, scores, users
from .settings import settings


def create_app(database_url: str | None = None) -> FastAPI:
    app = FastAPI(
        title="Помощник судьи — API",
        description="Реализация ТЗ «Приложение «Помощник судьи»»",
        version="2.0.0",
    )

    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    # Базы ранних версий: добавить недостающие колонки (ALTER TABLE)
    run_simple_migrations(engine)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.rate_limiter = RateLimiter(settings.RATE_LIMIT_PER_MIN, window_seconds=60.0)
    app.state.login_throttler = LoginThrottler(
        settings.LOGIN_MAX_ATTEMPTS, settings.LOGIN_WINDOW_MIN
    )

    # Настройка логирования: RELEASE-логи без ПД (ТЗ «Аудит и логирование»)
    level = logging.DEBUG if settings.DEBUG else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s %(message)s")

    @app.get("/health", tags=["service"], include_in_schema=False)
    def health() -> dict:
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(competitions.router)
    app.include_router(participants.router)
    app.include_router(scores.router)
    app.include_router(results.router)
    app.include_router(reports.router)

    # request_id + RELEASE-логи + rate limiting (после роутеров не важно,
    # middleware оборачивает всё приложение целиком)
    register_middleware(app)

    # Веб-интерфейс (одна страница, раздаётся без сборки)
    static_dir = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app
