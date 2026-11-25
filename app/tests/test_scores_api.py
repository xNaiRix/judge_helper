"""API-тесты оценок: валидация, права, upsert, «мои оценки» (SCORE-* кейсы)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from tests.conftest import auth, save_score

from judge_helper.models import Score


class TestSaveScore:
    def test_save_ok(self, client, users, started_competition):
        comp, p = started_competition["comp"], started_competition["p1"]
        r = save_score(client, users["admin"]["token"], comp["id"], p["id"], 5, 7, "Хорошо")
        assert r.status_code == 200
        body = r.json()
        assert body["artistry"] == 5
        assert body["technique"] == 7
        assert body["total"] == 12
        assert body["comment"] == "Хорошо"

    @pytest.mark.parametrize(
        "artistry,technique",
        [
            (11, 5),  # больше 10
            (5, 11),
            (-1, 5),  # отрицательное
            (5, -1),
            (2.5, 5),  # дробное
            ("abc", 5),  # нечисловое
        ],
    )
    def test_invalid_values_rejected(
        self, client, users, started_competition, artistry, technique
    ):
        comp, p = started_competition["comp"], started_competition["p1"]
        r = client.put(
            f"/competitions/{comp['id']}/scores/participants/{p['id']}",
            json={"artistry": artistry, "technique": technique},
            headers=auth(users["admin"]["token"]),
        )
        assert r.status_code == 422

    @pytest.mark.parametrize("artistry,technique", [(0, 0), (10, 10), (0, 10), (10, 0)])
    def test_boundary_values_allowed(
        self, client, users, started_competition, artistry, technique
    ):
        comp, p = started_competition["comp"], started_competition["p1"]
        r = save_score(client, users["admin"]["token"], comp["id"], p["id"], artistry, technique)
        assert r.status_code == 200

    def test_save_before_start_forbidden(self, client, users, competition):
        from tests.conftest import add_participant

        p = add_participant(client, users["admin"]["token"], competition["id"])
        r = save_score(client, users["admin"]["token"], competition["id"], p["id"], 5, 5)
        assert r.status_code == 403

    def test_save_after_finish_forbidden(self, client, users, started_competition):
        comp = started_competition["comp"]
        for p in (started_competition["p1"], started_competition["p2"]):
            save_score(client, users["admin"]["token"], comp["id"], p["id"], 5, 5)
        client.post(f"/competitions/{comp['id']}/finish", headers=auth(users["admin"]["token"]))
        r = save_score(
            client, users["admin"]["token"], comp["id"], started_competition["p1"]["id"], 1, 1
        )
        assert r.status_code == 403

    def test_outsider_cannot_score(self, client, users, started_competition):
        comp, p = started_competition["comp"], started_competition["p1"]
        r = save_score(client, users["outsider"]["token"], comp["id"], p["id"], 5, 5)
        assert r.status_code == 404

    def test_unknown_participant(self, client, users, started_competition):
        comp = started_competition["comp"]
        r = save_score(client, users["admin"]["token"], comp["id"], 999999, 5, 5)
        assert r.status_code == 404

    def test_upsert_updates_not_duplicates(self, client, users, started_competition):
        """Повторное сохранение обновляет запись, дубль не создаётся (ТЗ)."""
        comp, p = started_competition["comp"], started_competition["p1"]
        assert (
            save_score(client, users["admin"]["token"], comp["id"], p["id"], 3, 4).status_code
            == 200
        )
        assert (
            save_score(client, users["admin"]["token"], comp["id"], p["id"], 8, 6).status_code
            == 200
        )

        db = client.app.state.session_factory()
        scores = (
            db.execute(
                select(Score).where(
                    Score.competition_id == comp["id"],
                    Score.judge_id == users["admin"]["user"]["id"],
                    Score.participant_id == p["id"],
                )
            )
            .scalars()
            .all()
        )
        db.close()
        assert len(scores) == 1  # уникальность (judge, participant, competition)
        assert scores[0].artistry == 8
        assert scores[0].technique == 6

    def test_two_judges_both_can_score(self, client, users, started_competition):
        comp, p = started_competition["comp"], started_competition["p1"]
        assert (
            save_score(client, users["admin"]["token"], comp["id"], p["id"], 5, 5).status_code
            == 200
        )
        assert (
            save_score(client, users["judge"]["token"], comp["id"], p["id"], 7, 3).status_code
            == 200
        )
        db = client.app.state.session_factory()
        count = len(
            list(db.execute(select(Score).where(Score.participant_id == p["id"])).scalars())
        )
        db.close()
        assert count == 2


class TestMyScores:
    def test_my_scores_list(self, client, users, started_competition):
        comp = started_competition["comp"]
        save_score(
            client,
            users["admin"]["token"],
            comp["id"],
            started_competition["p1"]["id"],
            5,
            5,
            "комментарий",
        )
        save_score(
            client, users["judge"]["token"], comp["id"], started_competition["p1"]["id"], 9, 1
        )
        r = client.get(
            f"/competitions/{comp['id']}/scores/my", headers=auth(users["admin"]["token"])
        )
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 1  # видны только свои оценки
        assert items[0]["artistry"] == 5
        assert items[0]["total"] == 10
        assert items[0]["comment"] == "комментарий"
        assert items[0]["participant"]["last_name"] == "Кузнецов"

    def test_sorting_asc_desc(self, client, users, started_competition):
        comp = started_competition["comp"]
        p1, p2 = started_competition["p1"], started_competition["p2"]
        save_score(client, users["admin"]["token"], comp["id"], p1["id"], 3, 3)  # total 6
        save_score(client, users["admin"]["token"], comp["id"], p2["id"], 9, 9)  # total 18

        r = client.get(
            f"/competitions/{comp['id']}/scores/my?order=asc",
            headers=auth(users["admin"]["token"]),
        )
        totals = [i["total"] for i in r.json()]
        assert totals == [6, 18]

        r = client.get(
            f"/competitions/{comp['id']}/scores/my?order=desc",
            headers=auth(users["admin"]["token"]),
        )
        totals = [i["total"] for i in r.json()]
        assert totals == [18, 6]

    def test_sort_by_artistry(self, client, users, started_competition):
        comp = started_competition["comp"]
        p1, p2 = started_competition["p1"], started_competition["p2"]
        save_score(client, users["admin"]["token"], comp["id"], p1["id"], 2, 10)
        save_score(client, users["admin"]["token"], comp["id"], p2["id"], 8, 0)
        r = client.get(
            f"/competitions/{comp['id']}/scores/my?sort_by=artistry&order=asc",
            headers=auth(users["admin"]["token"]),
        )
        arts = [i["artistry"] for i in r.json()]
        assert arts == [2, 8]

    def test_filter_by_category(self, client, users, started_competition):
        comp = started_competition["comp"]
        p1, p2 = started_competition["p1"], started_competition["p2"]  # p2 — Юниоры
        save_score(client, users["admin"]["token"], comp["id"], p1["id"], 5, 5)
        save_score(client, users["admin"]["token"], comp["id"], p2["id"], 6, 6)
        r = client.get(
            f"/competitions/{comp['id']}/scores/my?category=Юниоры",
            headers=auth(users["admin"]["token"]),
        )
        items = r.json()
        assert len(items) == 1
        assert items[0]["participant"]["category"] == "Юниоры"

    def test_invalid_sort_field(self, client, users, started_competition):
        comp = started_competition["comp"]
        r = client.get(
            f"/competitions/{comp['id']}/scores/my?sort_by=height",
            headers=auth(users["admin"]["token"]),
        )
        assert r.status_code == 422

    def test_outsider_cannot_view(self, client, users, started_competition):
        comp = started_competition["comp"]
        r = client.get(
            f"/competitions/{comp['id']}/scores/my", headers=auth(users["outsider"]["token"])
        )
        assert r.status_code == 404
