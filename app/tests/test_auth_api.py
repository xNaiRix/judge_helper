"""API-тесты авторизации: регистрация, подтверждение email, JWT, сброс пароля,
Google OAuth feature flag, ограничение попыток входа (AUTH-* кейсы)."""

from __future__ import annotations

from tests.conftest import auth, raw_register, register_user


class TestRegister:
    def test_register_ok_no_tokens_until_confirmation(self, client):
        body = raw_register(client, "user@example.com", "Иванов Иван")
        assert body["user"]["email"] == "user@example.com"
        assert body["user"]["email_confirmed"] is False
        # access-токены не выдаются до подтверждения email (ТЗ)
        assert "access_token" not in body
        # демо-режим возвращает токен подтверждения
        assert body["confirmation_token"]

    def test_register_without_adult_consent(self, client):
        r = client.post(
            "/auth/register",
            json={
                "email": "user@example.com",
                "full_name": "Иванов Иван",
                "password": "Passw0rd!",
                "adult": False,
            },
        )
        assert r.status_code == 422

    def test_register_invalid_email(self, client):
        r = client.post(
            "/auth/register",
            json={
                "email": "not-an-email",
                "full_name": "Иванов Иван",
                "password": "Passw0rd!",
                "adult": True,
            },
        )
        assert r.status_code == 422

    def test_register_short_password(self, client):
        r = client.post(
            "/auth/register",
            json={
                "email": "user@example.com",
                "full_name": "Иванов Иван",
                "password": "123",
                "adult": True,
            },
        )
        assert r.status_code == 422

    def test_register_duplicate_email(self, client):
        raw_register(client, "dup@example.com", "Иванов Иван")
        r = client.post(
            "/auth/register",
            json={
                "email": "dup@example.com",
                "full_name": "Другой Человек",
                "password": "Passw0rd!",
                "adult": True,
            },
        )
        assert r.status_code == 409


class TestEmailConfirmation:
    def test_login_blocked_until_confirmed(self, client):
        raw_register(client, "lock@example.com", "Иванов Иван", password="Secret123")
        r = client.post("/auth/login", json={"email": "lock@example.com", "password": "Secret123"})
        assert r.status_code == 403
        assert "подтверд" in r.json()["detail"].lower()

    def test_confirm_email_flow(self, client):
        reg = raw_register(client, "ok@example.com", "Иванов Иван", password="Secret123")
        r = client.post("/auth/confirm-email", json={"token": reg["confirmation_token"]})
        assert r.status_code == 200
        # После подтверждения вход работает
        r = client.post("/auth/login", json={"email": "ok@example.com", "password": "Secret123"})
        assert r.status_code == 200
        assert r.json()["access_token"] and r.json()["refresh_token"]

    def test_confirm_twice_rejected(self, client):
        reg = raw_register(client, "twice@example.com", "Иванов Иван")
        token = reg["confirmation_token"]
        assert client.post("/auth/confirm-email", json={"token": token}).status_code == 200
        assert client.post("/auth/confirm-email", json={"token": token}).status_code == 400

    def test_confirm_bad_token(self, client):
        r = client.post("/auth/confirm-email", json={"token": "no-such-token"})
        assert r.status_code == 400

    def test_confirm_expired_token(self, client):
        from datetime import timedelta

        from judge_helper.models import AuthToken, utcnow

        reg = raw_register(client, "exp@example.com", "Иванов Иван")
        db = client.app.state.session_factory()
        token_row = db.query(AuthToken).filter_by(token=reg["confirmation_token"]).one()
        token_row.expires_at = utcnow() - timedelta(hours=25)
        db.commit()
        db.close()
        r = client.post("/auth/confirm-email", json={"token": reg["confirmation_token"]})
        assert r.status_code == 410


class TestLoginAndTokens:
    def test_login_wrong_password(self, client):
        register_user(client, "login@example.com", "Иванов Иван", password="Secret123")
        r = client.post("/auth/login", json={"email": "login@example.com", "password": "Wrong123"})
        assert r.status_code == 401

    def test_login_unknown_email(self, client):
        r = client.post(
            "/auth/login", json={"email": "ghost@example.com", "password": "Whatever1"}
        )
        assert r.status_code == 401

    def test_me(self, client):
        body = register_user(client, "me@example.com", "Иванов Иван")
        r = client.get("/auth/me", headers=auth(body["token"]))
        assert r.status_code == 200
        assert r.json()["email"] == "me@example.com"
        assert client.get("/auth/me").status_code == 401
        assert client.get("/auth/me", headers=auth("bad")).status_code == 401

    def test_refresh_tokens(self, client):
        body = register_user(client, "ref@example.com", "Иванов Иван")
        r = client.post("/auth/refresh", json={"refresh_token": body["refresh_token"]})
        assert r.status_code == 200
        refreshed = r.json()
        assert refreshed["access_token"]
        # Новый access работает
        assert client.get("/auth/me", headers=auth(refreshed["access_token"])).status_code == 200

    def test_refresh_rejects_access_token(self, client):
        body = register_user(client, "ref2@example.com", "Иванов Иван")
        r = client.post("/auth/refresh", json={"refresh_token": body["token"]})
        assert r.status_code == 401

    def test_logout_revokes_tokens(self, client):
        body = register_user(client, "out@example.com", "Иванов Иван")
        r = client.post(
            "/auth/logout",
            headers=auth(body["token"]),
            json={"refresh_token": body["refresh_token"]},
        )
        assert r.status_code == 204
        # Access отозван
        assert client.get("/auth/me", headers=auth(body["token"])).status_code == 401
        # Refresh отозван
        assert (
            client.post("/auth/refresh", json={"refresh_token": body["refresh_token"]}).status_code
            == 401
        )

    def test_tampered_token_rejected(self, client):
        register_user(client, "tam@example.com", "Иванов Иван")
        # Подпись/алгоритм проверяются: испорченный токен отклоняется
        tampered = "eyJhbGciOiJIUzI1NiJ9.broken.sig"
        assert client.get("/auth/me", headers=auth(tampered)).status_code == 401


class TestLoginRateLimit:
    def test_five_failures_then_blocked(self, client):
        register_user(client, "brute@example.com", "Иванов Иван", password="Secret123")
        for i in range(5):
            r = client.post(
                "/auth/login", json={"email": "brute@example.com", "password": f"Wrong{i}!"}
            )
            assert r.status_code == 401
        # 6-я попытка блокируется, даже с верным паролем (ТЗ: 5 за 15 минут)
        r = client.post(
            "/auth/login", json={"email": "brute@example.com", "password": "Secret123"}
        )
        assert r.status_code == 429


class TestPasswordReset:
    def test_forgot_and_reset(self, client):
        body = register_user(client, "reset@example.com", "Иванов Иван", password="OldPass123")
        old_access = body["token"]

        r = client.post("/auth/forgot-password", json={"email": "reset@example.com"})
        assert r.status_code == 200
        reset_token = r.json()["reset_token"]
        assert reset_token

        r = client.post(
            "/auth/reset-password", json={"token": reset_token, "new_password": "NewPass456"}
        )
        assert r.status_code == 200
        assert r.json()["sessions_terminated"] is True

        # Старая сессия завершена (ТЗ)
        assert client.get("/auth/me", headers=auth(old_access)).status_code == 401
        # Вход с новым паролем работает
        r = client.post(
            "/auth/login", json={"email": "reset@example.com", "password": "NewPass456"}
        )
        assert r.status_code == 200

    def test_reset_token_single_use(self, client):
        register_user(client, "reuse@example.com", "Иванов Иван", password="OldPass123")
        reset_token = client.post(
            "/auth/forgot-password", json={"email": "reuse@example.com"}
        ).json()["reset_token"]
        assert (
            client.post(
                "/auth/reset-password",
                json={"token": reset_token, "new_password": "NewPass456"},
            ).status_code
            == 200
        )
        # Повторное использование токена невозможно
        assert (
            client.post(
                "/auth/reset-password",
                json={"token": reset_token, "new_password": "Again789"},
            ).status_code
            == 400
        )

    def test_reset_bad_token(self, client):
        r = client.post(
            "/auth/reset-password", json={"token": "no-such", "new_password": "Whatever123"}
        )
        assert r.status_code == 400

    def test_forgot_unknown_email_same_response(self, client):
        """Существование аккаунта не раскрывается."""
        r = client.post("/auth/forgot-password", json={"email": "ghost@example.com"})
        assert r.status_code == 200
        assert r.json()["reset_token"] is None


class TestGoogleOAuthFeatureFlag:
    def test_disabled_returns_404(self, client, monkeypatch):
        from judge_helper.settings import settings as app_settings

        monkeypatch.setattr(app_settings, "GOOGLE_OAUTH_ENABLED", False)
        assert client.get("/auth/google/url").status_code == 404
        assert client.post("/auth/google/callback").status_code == 404

    def test_enabled_without_client_id_501(self, client, monkeypatch):
        from judge_helper.settings import settings as app_settings

        monkeypatch.setattr(app_settings, "GOOGLE_OAUTH_ENABLED", True)
        monkeypatch.setattr(app_settings, "GOOGLE_CLIENT_ID", "")
        assert client.get("/auth/google/url").status_code == 501

    def test_enabled_returns_url(self, client, monkeypatch):
        from judge_helper.settings import settings as app_settings

        monkeypatch.setattr(app_settings, "GOOGLE_OAUTH_ENABLED", True)
        monkeypatch.setattr(app_settings, "GOOGLE_CLIENT_ID", "test-client-id")
        r = client.get("/auth/google/url")
        assert r.status_code == 200
        assert "accounts.google.com" in r.json()["url"]
        assert "test-client-id" in r.json()["url"]


class TestHealth:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
