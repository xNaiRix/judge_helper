"""Тесты миграции баз ранних версий (ALTER TABLE + пересборка users)."""

from __future__ import annotations

from sqlalchemy import create_engine, text

from judge_helper.main import create_app
from judge_helper.models import User

OLD_USERS_DDL = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    email VARCHAR(255) UNIQUE,
    full_name VARCHAR(255),
    password_hash VARCHAR(512),
    token VARCHAR(64) NOT NULL,
    created_at DATETIME
)
"""


def _make_old_db(path) -> str:
    """База со схемой первой версии приложения и одним пользователем."""
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(text(OLD_USERS_DDL))
        conn.execute(
            text(
                "INSERT INTO users "
                "(email, full_name, password_hash, token, created_at) "
                "VALUES ('old@example.com', 'Старый Пользователь', 'x', 'y', "
                "'2026-11-01 00:00:00')"
            )
        )
    engine.dispose()
    return f"sqlite:///{path}"


class TestSimpleMigrations:
    def test_old_users_migrated_and_register_works(self, tmp_path):
        """Старая база с NOT NULL-колонкой token: миграция + новая регистрация."""
        from fastapi.testclient import TestClient

        url = _make_old_db(tmp_path / "old.db")
        app = create_app(database_url=url)
        with TestClient(app) as client:
            db = app.state.session_factory()
            user = db.query(User).filter_by(email="old@example.com").one()
            db.close()
            # Старые пользователи считаются подтверждёнными — вход не блокируется
            assert user.email_confirmed is True
            assert user.token_version == 0
            assert user.notifications_enabled is True

            # Колонка token удалена — регистрация новых пользователей работает
            r = client.post(
                "/auth/register",
                json={
                    "email": "new@example.com",
                    "full_name": "Новый Пользователь",
                    "password": "Passw0rd!",
                    "adult": True,
                },
            )
            assert r.status_code == 201, r.text
            r = client.post("/auth/confirm-email", json={"token": r.json()["confirmation_token"]})
            assert r.status_code == 200
            r = client.post(
                "/auth/login", json={"email": "new@example.com", "password": "Passw0rd!"}
            )
            assert r.status_code == 200

            db = app.state.session_factory()
            names = {u.email for u in db.query(User).all()}
            db.close()
            assert names == {"old@example.com", "new@example.com"}

    def test_migration_idempotent(self, tmp_path):
        url = _make_old_db(tmp_path / "old2.db")
        create_app(database_url=url)  # первая миграция
        app = create_app(database_url=url)  # повторный запуск — без ошибок
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
