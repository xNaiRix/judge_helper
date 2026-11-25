"""Тесты ограничений: 100 запросов/мин с одного IP (SEC-* кейсы ТЗ)."""

from __future__ import annotations


class TestRateLimit:
    def test_limit_100_per_minute_per_ip(self, client):
        # Снижаем лимит для теста (лимит читается из состояния приложения)
        client.app.state.rate_limiter.limit = 10
        for _ in range(10):
            assert client.get("/health").status_code == 200
        # 11-й запрос за минуту с того же IP — 429
        r = client.get("/health")
        assert r.status_code == 429
        assert "лимит" in r.json()["detail"].lower()

    def test_request_id_header(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        # Каждый запрос получает идентификатор (для RELEASE-логов и аудита)
        assert r.headers.get("X-Request-ID")
