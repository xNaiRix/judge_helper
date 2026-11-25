"""Тесты веб-интерфейса: раздача статической страницы (UI-* кейсы)."""

from __future__ import annotations


class TestWebUI:
    def test_index_served(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        # Заголовок и ключевые экраны присутствуют в разметке
        assert "Помощник судьи" in r.text
        for marker in (
            "view-auth",  # экран входа/регистрации
            "view-list",  # список соревнований
            "view-comp",  # карточка соревнования
            "card-invitations",  # приглашения (принять/отклонить)
            "form-add-participant",  # добавление участников
            "card-scoring",  # выставление оценок
            "card-results",  # результаты
        ):
            assert marker in r.text, f"Не найден элемент интерфейса: {marker}"

    def test_index_contains_auth_logic(self, client):
        r = client.get("/")
        # Веб-интерфейс работает с тем же API и JWT-заголовком Authorization
        assert "/auth/register" in r.text
        assert "/auth/login" in r.text
        assert "Authorization" in r.text
        assert "Bearer" in r.text

    def test_api_still_available_alongside_static(self, client):
        # Монтирование статики на "/" не ломает API-маршруты
        assert client.get("/health").status_code == 200
