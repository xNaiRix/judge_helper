"""API-тесты результатов: расчёты, округление, сортировка, фильтры (RES-* кейсы)."""

from __future__ import annotations

import pytest
from tests.conftest import (
    add_judge,
    add_participant,
    auth,
    register_user,
    save_score,
    start_competition,
)


def _finish(client, admin_token, comp_id):
    r = client.post(f"/competitions/{comp_id}/finish", headers=auth(admin_token))
    assert r.status_code == 200, r.text


class TestAccess:
    def test_results_before_finish(self, client, users, started_competition):
        comp = started_competition["comp"]
        r = client.get(
            f"/competitions/{comp['id']}/results", headers=auth(users["admin"]["token"])
        )
        assert r.status_code == 409

    def test_outsider_cannot_view_results(self, client, users, started_competition):
        comp = started_competition["comp"]
        r = client.get(
            f"/competitions/{comp['id']}/results", headers=auth(users["outsider"]["token"])
        )
        assert r.status_code == 404


class TestCalculations:
    def test_averages_exclude_missing_judge(self, client, users, started_competition):
        """Судья, не оценивший участника, не включается в средние (ТЗ)."""
        comp, p1, p2 = (
            started_competition["comp"],
            started_competition["p1"],
            started_competition["p2"],
        )
        # p1 оценивают оба судьи: (5+5)=10 и (7+3)=10 → среднее 10.0
        save_score(client, users["admin"]["token"], comp["id"], p1["id"], 5, 5)
        save_score(client, users["judge"]["token"], comp["id"], p1["id"], 7, 3)
        # p2 оценивает только администратор: (8+6)=14 → среднее 14.0
        save_score(client, users["admin"]["token"], comp["id"], p2["id"], 8, 6)
        _finish(client, users["admin"]["token"], comp["id"])

        r = client.get(
            f"/competitions/{comp['id']}/results", headers=auth(users["judge"]["token"])
        )
        assert r.status_code == 200
        rows = r.json()
        by_id = {row["participant"]["id"]: row for row in rows}

        assert by_id[p1["id"]]["competition_score"] == 10.0
        assert by_id[p1["id"]]["artistry_score"] == 6.0  # (5+7)/2
        assert by_id[p1["id"]]["technique_score"] == 4.0  # (5+3)/2

        assert by_id[p2["id"]]["competition_score"] == 14.0
        assert by_id[p2["id"]]["artistry_score"] == 8.0
        assert by_id[p2["id"]]["technique_score"] == 6.0

    def test_rounding_to_two_decimals(self, client, users, competition):
        """Три судьи: проверяем округление средних до 2 знаков."""
        judge3 = register_user(client, "judge3@example.com", "Третий Судья Третьевич")
        add_judge(client, users["admin"]["token"], users["judge"], competition["id"])
        add_judge(client, users["admin"]["token"], judge3, competition["id"])
        p = add_participant(
            client,
            users["admin"]["token"],
            competition["id"],
            last_name="Один",
            first_name="Одинов",
        )
        start_competition(client, users["admin"]["token"], competition["id"])

        # артистизм: 5, 5, 4 → 14/3 = 4.666… → 4.67
        # техника:   5, 5, 5 → 5.0
        # итог судьи: 10, 10, 9 → 29/3 = 9.666… → 9.67
        save_score(client, users["admin"]["token"], competition["id"], p["id"], 5, 5)
        save_score(client, users["judge"]["token"], competition["id"], p["id"], 5, 5)
        save_score(client, judge3["token"], competition["id"], p["id"], 4, 5)
        _finish(client, users["admin"]["token"], competition["id"])

        r = client.get(
            f"/competitions/{competition['id']}/results", headers=auth(users["admin"]["token"])
        )
        row = r.json()[0]
        assert row["artistry_score"] == 4.67
        assert row["technique_score"] == 5.0
        assert row["competition_score"] == 9.67


class TestSortingFiltering:
    @pytest.fixture()
    def finished(self, client, users, started_competition):
        comp, p1, p2 = (
            started_competition["comp"],
            started_competition["p1"],
            started_competition["p2"],
        )
        save_score(client, users["admin"]["token"], comp["id"], p1["id"], 3, 3)  # 6.0, Взрослые
        save_score(client, users["judge"]["token"], comp["id"], p1["id"], 5, 3)  # +8 → 7.0
        save_score(client, users["admin"]["token"], comp["id"], p2["id"], 9, 9)  # 18.0, Юниоры
        save_score(client, users["judge"]["token"], comp["id"], p2["id"], 7, 5)  # +12 → 15.0
        _finish(client, users["admin"]["token"], comp["id"])
        return {"comp": comp, "p1": p1, "p2": p2}

    def test_default_sort_desc_by_competition_score(self, client, users, finished):
        r = client.get(
            f"/competitions/{finished['comp']['id']}/results",
            headers=auth(users["admin"]["token"]),
        )
        scores = [row["competition_score"] for row in r.json()]
        assert scores == [15.0, 7.0]

    def test_sort_asc(self, client, users, finished):
        r = client.get(
            f"/competitions/{finished['comp']['id']}/results?order=asc",
            headers=auth(users["admin"]["token"]),
        )
        scores = [row["competition_score"] for row in r.json()]
        assert scores == [7.0, 15.0]

    def test_sort_by_technique(self, client, users, finished):
        # техника: p1 = (3+3)/2 = 3.0; p2 = (9+5)/2 = 7.0
        r = client.get(
            f"/competitions/{finished['comp']['id']}/results?sort_by=technique_score&order=asc",
            headers=auth(users["admin"]["token"]),
        )
        tech = [row["technique_score"] for row in r.json()]
        assert tech == [3.0, 7.0]

    def test_sort_by_artistry(self, client, users, finished):
        # артистизм: p1 = (3+5)/2 = 4.0; p2 = (9+7)/2 = 8.0
        r = client.get(
            f"/competitions/{finished['comp']['id']}/results?sort_by=artistry_score&order=asc",
            headers=auth(users["admin"]["token"]),
        )
        art = [row["artistry_score"] for row in r.json()]
        assert art == [4.0, 8.0]

    def test_filter_by_category(self, client, users, finished):
        r = client.get(
            f"/competitions/{finished['comp']['id']}/results?category=Юниоры",
            headers=auth(users["admin"]["token"]),
        )
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["participant"]["id"] == finished["p2"]["id"]

    def test_filter_empty_category(self, client, users, finished):
        r = client.get(
            f"/competitions/{finished['comp']['id']}/results?category=Дети",
            headers=auth(users["admin"]["token"]),
        )
        assert r.json() == []

    def test_invalid_sort_field(self, client, users, finished):
        r = client.get(
            f"/competitions/{finished['comp']['id']}/results?sort_by=weight",
            headers=auth(users["admin"]["token"]),
        )
        assert r.status_code == 422


class TestAdminBreakdown:
    def test_admin_sees_judges_breakdown(self, client, users, started_competition):
        comp, p1 = started_competition["comp"], started_competition["p1"]
        save_score(client, users["admin"]["token"], comp["id"], p1["id"], 5, 5, "Моё мнение")
        save_score(client, users["judge"]["token"], comp["id"], p1["id"], 7, 3)
        save_score(
            client, users["admin"]["token"], comp["id"], started_competition["p2"]["id"], 8, 6
        )
        _finish(client, users["admin"]["token"], comp["id"])

        # Администратор видит, кто сколько поставил (ТЗ)
        r = client.get(
            f"/competitions/{comp['id']}/results", headers=auth(users["admin"]["token"])
        )
        row = next(x for x in r.json() if x["participant"]["id"] == p1["id"])
        assert row["judges"] is not None
        assert len(row["judges"]) == 2
        by_email = {j["email"]: j for j in row["judges"]}
        admin_j = by_email[users["admin"]["user"]["email"]]
        assert admin_j["artistry"] == 5
        assert admin_j["technique"] == 5
        assert admin_j["total"] == 10
        assert admin_j["comment"] == "Моё мнение"

    def test_judge_does_not_see_breakdown(self, client, users, started_competition):
        """Обычный судья не видит оценки других судей (ТЗ)."""
        comp, p1 = started_competition["comp"], started_competition["p1"]
        save_score(client, users["admin"]["token"], comp["id"], p1["id"], 5, 5)
        save_score(client, users["judge"]["token"], comp["id"], p1["id"], 7, 3)
        save_score(
            client, users["admin"]["token"], comp["id"], started_competition["p2"]["id"], 8, 6
        )
        _finish(client, users["admin"]["token"], comp["id"])

        r = client.get(
            f"/competitions/{comp['id']}/results", headers=auth(users["judge"]["token"])
        )
        for row in r.json():
            assert row["judges"] is None
