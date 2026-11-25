"""Тесты отчётов: статистика по судьям, CSV, PNG, PDF-протокол (REP-* кейсы)."""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import auth

from judge_helper.models import Competition, utcnow


class TestJudgesStats:
    def test_stats_values(self, client, users, finished_competition):
        comp = finished_competition["comp"]
        r = client.get(
            f"/competitions/{comp['id']}/judges/stats", headers=auth(users["admin"]["token"])
        )
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 2  # оба судьи

        by_email = {row["email"]: row for row in rows}
        # admin ставил всем (5,5); judge — (7,3)
        admin = by_email[users["admin"]["user"]["email"]]
        judge = by_email[users["judge"]["user"]["email"]]
        assert admin["avg_artistry"] == 5.0
        assert admin["avg_technique"] == 5.0
        assert admin["avg_total"] == 10.0
        assert judge["avg_artistry"] == 7.0
        assert judge["avg_technique"] == 3.0

        # Развёрнутый список: участник — баллы судьи — итоговые баллы
        entry = admin["entries"][0]
        assert entry["artistry"] == 5
        assert entry["total"] == 10
        assert entry["competition_score"] == 10.0  # (10 + 10) / 2

    def test_stats_before_finish(self, client, users, started_competition):
        comp = started_competition["comp"]
        r = client.get(
            f"/competitions/{comp['id']}/judges/stats", headers=auth(users["admin"]["token"])
        )
        assert r.status_code == 409

    def test_stats_outsider(self, client, users, finished_competition):
        comp = finished_competition["comp"]
        r = client.get(
            f"/competitions/{comp['id']}/judges/stats", headers=auth(users["outsider"]["token"])
        )
        assert r.status_code == 404


class TestCsv:
    def test_csv_content(self, client, users, finished_competition):
        comp = finished_competition["comp"]
        r = client.get(
            f"/competitions/{comp['id']}/reports/chart.csv", headers=auth(users["judge"]["token"])
        )
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        assert "attachment" in r.headers.get("content-disposition", "")

        text = r.text
        assert text.startswith("\ufeff")  # BOM для Excel
        lines = text.lstrip("\ufeff").strip().splitlines()
        assert lines[0].split(";") == [
            "Категория",
            "Показатель",
            "Среднее",
            "Максимум",
            "Минимум",
            "Медиана",
        ]
        # Две категории (Взрослые, Юниоры) × 3 показателя
        assert len(lines) == 1 + 2 * 3
        # Взрослые: оба судьи (10 и 10) → среднее 10,00.
        # Десятичная запятая: Excel в русской локали не превращает баллы в даты
        assert any(line.startswith("Взрослые;Соревновательный балл;10,00") for line in lines)
        for line in lines[1:]:
            values = line.split(";")[2:]
            assert all("," in v for v in values)
            assert all("." not in v for v in values)

    def test_csv_before_finish(self, client, users, started_competition):
        comp = started_competition["comp"]
        r = client.get(
            f"/competitions/{comp['id']}/reports/chart.csv", headers=auth(users["admin"]["token"])
        )
        assert r.status_code == 409


class TestPng:
    def test_png_bytes(self, client, users, finished_competition):
        comp = finished_competition["comp"]
        r = client.get(
            f"/competitions/{comp['id']}/reports/chart.png", headers=auth(users["judge"]["token"])
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"  # PNG-магия
        assert len(r.content) > 5000

    def test_single_category(self, client, users, finished_competition):
        comp = finished_competition["comp"]
        r = client.get(
            f"/competitions/{comp['id']}/reports/chart.png?category=Юниоры",
            headers=auth(users["judge"]["token"]),
        )
        assert r.status_code == 200

    def test_unknown_category(self, client, users, finished_competition):
        comp = finished_competition["comp"]
        r = client.get(
            f"/competitions/{comp['id']}/reports/chart.png?category=Дети",
            headers=auth(users["judge"]["token"]),
        )
        assert r.status_code == 422


class TestPdfProtocol:
    def test_pdf_generated(self, client, users, finished_competition):
        comp = finished_competition["comp"]
        r = client.get(
            f"/competitions/{comp['id']}/protocol.pdf", headers=auth(users["judge"]["token"])
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.content[:5] == b"%PDF-"
        assert len(r.content) > 1000

    def test_pdf_deterministic(self, client, users, finished_competition):
        """При неизменных данных повторная генерация идентична (ТЗ)."""
        comp = finished_competition["comp"]
        headers = auth(users["admin"]["token"])
        first = client.get(f"/competitions/{comp['id']}/protocol.pdf", headers=headers).content
        second = client.get(f"/competitions/{comp['id']}/protocol.pdf", headers=headers).content
        assert first == second

    def test_pdf_before_finish(self, client, users, started_competition):
        comp = started_competition["comp"]
        r = client.get(
            f"/competitions/{comp['id']}/protocol.pdf", headers=auth(users["admin"]["token"])
        )
        assert r.status_code == 409

    def test_pdf_outsider(self, client, users, finished_competition):
        comp = finished_competition["comp"]
        r = client.get(
            f"/competitions/{comp['id']}/protocol.pdf", headers=auth(users["outsider"]["token"])
        )
        assert r.status_code == 404


class TestAutoDelete:
    def test_finished_year_ago_autodeleted(self, client, users, finished_competition):
        """Соревнование автоматически удаляется через год после завершения (ТЗ)."""
        comp = finished_competition["comp"]

        # Имитируем завершение год назад
        db = client.app.state.session_factory()
        row = db.get(Competition, comp["id"])
        row.finished_at = utcnow() - timedelta(days=366)
        db.commit()
        db.close()

        # sweep срабатывает при запросе списка
        r = client.get("/competitions", headers=auth(users["admin"]["token"]))
        assert all(c["id"] != comp["id"] for c in r.json())

        # Прямой доступ закрыт
        assert (
            client.get(
                f"/competitions/{comp['id']}", headers=auth(users["admin"]["token"])
            ).status_code
            == 404
        )

        # Автоудаление НЕ сопровождается уведомлением (ТЗ: только ручное)
        notes = client.get("/notifications", headers=auth(users["judge"]["token"])).json()
        assert not any(n["kind"] == "deleted" for n in notes)

    def test_recently_finished_stays(self, client, users, finished_competition):
        comp = finished_competition["comp"]
        r = client.get("/competitions", headers=auth(users["admin"]["token"]))
        assert any(c["id"] == comp["id"] for c in r.json())
