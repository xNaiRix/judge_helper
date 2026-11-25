"""Настройка подключения к базе данных.

По умолчанию используется SQLite (для учебного/упрощённого режима).
Через переменную окружения DATABASE_URL можно подключить PostgreSQL.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

DEFAULT_URL = "sqlite:///./judge_helper.db"


class Base(DeclarativeBase):
    """Базовый класс всех ORM-моделей."""


def make_engine(url: str | None = None):
    """Создаёт engine. Для in-memory SQLite используется StaticPool,
    чтобы соединение было общим для всех сессий приложения."""
    url = url or os.environ.get("DATABASE_URL", DEFAULT_URL)
    if url.startswith("sqlite"):
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
        return create_engine(url, **kwargs)
    return create_engine(url)


def make_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# Недостающие колонки в базах, созданных ранними версиями приложения.
# CREATE TABLE их не добавляет — поэтому патчим через ALTER TABLE.
_MIGRATION_COLUMNS = {
    "users": [
        ("email_confirmed", "BOOLEAN NOT NULL DEFAULT 1"),
        ("token_version", "INTEGER NOT NULL DEFAULT 0"),
        ("notifications_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
        ("email_notifications_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
    ],
    "competitions": [
        ("finished_at", "DATETIME"),
    ],
    "scores": [
        ("client_time", "DATETIME"),
        ("server_received_at", "DATETIME"),
    ],
}


def _rebuild_users_without_legacy_token(conn) -> None:
    """Пересборка users без устаревшей колонки token (NOT NULL из v1).

    SQLite не позволяет менять ограничения колонок, поэтому: создаём новую таблицу
    по актуальной схеме, копируем данные, удаляем старую, переименовываем.
    """
    conn.execute(
        text("""
        CREATE TABLE users_new (
            id INTEGER PRIMARY KEY,
            email VARCHAR(255),
            full_name VARCHAR(255),
            password_hash VARCHAR(512),
            email_confirmed BOOLEAN DEFAULT 1,
            token_version INTEGER DEFAULT 0,
            notifications_enabled BOOLEAN DEFAULT 1,
            email_notifications_enabled BOOLEAN DEFAULT 0,
            created_at DATETIME
        )
    """)
    )
    conn.execute(
        text("""
        INSERT INTO users_new (id, email, full_name, password_hash, email_confirmed,
                               token_version, notifications_enabled,
                               email_notifications_enabled, created_at)
        SELECT id, email, full_name, password_hash,
               COALESCE(email_confirmed, 1), COALESCE(token_version, 0),
               COALESCE(notifications_enabled, 1), COALESCE(email_notifications_enabled, 0),
               created_at
        FROM users
    """)
    )
    conn.execute(text("DROP TABLE users"))
    conn.execute(text("ALTER TABLE users_new RENAME TO users"))
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)"))


def run_simple_migrations(engine) -> None:
    """Миграция баз ранних версий:

    * добавление недостающих колонок (ALTER TABLE);
    * пересборка users без устаревшей NOT NULL-колонки token (v1).

    Пользователи, существовавшие до введения подтверждения email,
    считаются подтверждёнными (DEFAULT 1), чтобы не блокировать их вход.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    with engine.begin() as conn:
        users_columns = set()
        if inspector.has_table("users"):
            users_columns = {c["name"] for c in inspector.get_columns("users")}

        for table, columns in _MIGRATION_COLUMNS.items():
            if not inspector.has_table(table):
                continue  # таблица будет создана create_all с актуальной схемой
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))

        # Устаревшая колонка token (NOT NULL в v1) мешает вставке новых
        # пользователей — пересобираем таблицу без неё.
        if "token" in users_columns:
            _rebuild_users_without_legacy_token(conn)
