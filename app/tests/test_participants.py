"""API-тесты участников: карточки, дубли ФИО, категории, лимиты (PART-* кейсы)."""

from __future__ import annotations

import pytest

from tests.conftest import add_participant, auth


def _add(client, token, cid, **kw):
    return client.post(
        f"/competitions/{cid}/participants",
        json={
            "last_name": kw.get("last_name", "Тестов"),
            "first_name": kw.get("first_name", "Тест"),
            "patronymic": kw.get("patronymic", ""),
            "category": kw.get("category", "Взрослые"),
            "comment": kw.get("comment", ""),
        },
        headers=auth(token),
    )


class TestCreate:
    def test_add_participant(self, client, users, competition):
        r = _add(
            client,
            users["admin"]["token"],
            competition["id"],
            last_name="Смирнов",
            first_name="Олег",
            patronymic="Иванович",
        )
        assert r.status_code == 201
        body = r.json()
        assert body["last_name"] == "Смирнов"
        assert body["category"] == "Взрослые"

    @pytest.mark.parametrize("category", ["Дети", "Юниоры", "Взрослые", "Ветераны"])
    def test_all_categories_allowed(self, client, users, competition, category):
        r = _add(client, users["admin"]["token"], competition["id"], category=category)
        assert r.status_code == 201
        assert r.json()["category"] == category

    def test_invalid_category_rejected(self, client, users, competition):
        r = _add(client, users["admin"]["token"], competition["id"], category="Суперстар")
        assert r.status_code == 422

    def test_empty_last_name_rejected(self, client, users, competition):
        r = _add(client, users["admin"]["token"], competition["id"], last_name="")
        assert r.status_code == 422

    def test_add_by_non_admin_forbidden(self, client, users, competition):
        from tests.conftest import add_judge

        add_judge(client, users["admin"]["token"], users["judge"], competition["id"])
        r = _add(client, users["judge"]["token"], competition["id"])
        assert r.status_code == 403

    def test_add_by_outsider_not_found(self, client, users, competition):
        r = _add(client, users["outsider"]["token"], competition["id"])
        assert r.status_code == 404

    def test_add_after_start_forbidden(self, client, users, started_competition):
        comp = started_competition["comp"]
        r = _add(client, users["admin"]["token"], comp["id"])
        assert r.status_code == 403


class TestDuplicates:
    def test_exact_duplicate(self, client, users, competition):
        add_participant(
            client,
            users["admin"]["token"],
            competition["id"],
            last_name="Смирнов",
            first_name="Олег",
            patronymic="Иванович",
        )
        r = _add(
            client,
            users["admin"]["token"],
            competition["id"],
            last_name="Смирнов",
            first_name="Олег",
            patronymic="Иванович",
        )
        assert r.status_code == 409
        assert "уже существует" in r.json()["detail"]["message"]
        assert r.json()["detail"]["existing"]["last_name"] == "Смирнов"

    def test_duplicate_after_normalisation(self, client, users, competition):
        # Разные регистр, пробелы и «ё»/«е» — всё равно дубль
        add_participant(
            client,
            users["admin"]["token"],
            competition["id"],
            last_name="Артёмов",
            first_name="Артём",
            patronymic="Артёмович",
        )
        r = _add(
            client,
            users["admin"]["token"],
            competition["id"],
            last_name="  аРтЁмОв ",
            first_name=" артем  ",
            patronymic="Артёмович",
        )
        assert r.status_code == 409

    def test_hyphen_makes_difference(self, client, users, competition):
        # Дефис — значимый символ, это не дубль
        add_participant(
            client,
            users["admin"]["token"],
            competition["id"],
            last_name="Римский-Корсаков",
            first_name="Николай",
        )
        r = _add(
            client,
            users["admin"]["token"],
            competition["id"],
            last_name="Римский Корсаков",
            first_name="Николай",
        )
        assert r.status_code == 201

    def test_different_patronymic_not_duplicate(self, client, users, competition):
        add_participant(
            client,
            users["admin"]["token"],
            competition["id"],
            last_name="Смирнов",
            first_name="Олег",
            patronymic="Иванович",
        )
        r = _add(
            client,
            users["admin"]["token"],
            competition["id"],
            last_name="Смирнов",
            first_name="Олег",
            patronymic="Петрович",
        )
        assert r.status_code == 201

    def test_duplicate_in_other_competition_allowed(self, client, users, competition):
        from tests.conftest import create_competition

        add_participant(
            client,
            users["admin"]["token"],
            competition["id"],
            last_name="Смирнов",
            first_name="Олег",
        )
        other = create_competition(client, users["admin"]["token"], name="Другое")
        r = _add(
            client, users["admin"]["token"], other["id"], last_name="Смирнов", first_name="Олег"
        )
        assert r.status_code == 201


class TestUpdateDelete:
    def test_update_fields(self, client, users, competition):
        p = add_participant(
            client,
            users["admin"]["token"],
            competition["id"],
            last_name="Смирнов",
            first_name="Олег",
        )
        r = client.patch(
            f"/competitions/{competition['id']}/participants/{p['id']}",
            json={"category": "Ветераны", "comment": "Пришёл с травмой"},
            headers=auth(users["admin"]["token"]),
        )
        assert r.status_code == 200
        assert r.json()["category"] == "Ветераны"
        assert r.json()["comment"] == "Пришёл с травмой"

    def test_update_to_duplicate_fio(self, client, users, competition):
        add_participant(
            client,
            users["admin"]["token"],
            competition["id"],
            last_name="Смирнов",
            first_name="Олег",
        )
        p2 = add_participant(
            client,
            users["admin"]["token"],
            competition["id"],
            last_name="Козлов",
            first_name="Дмитрий",
        )
        r = client.patch(
            f"/competitions/{competition['id']}/participants/{p2['id']}",
            json={"last_name": "Смирнов", "first_name": "Олег"},
            headers=auth(users["admin"]["token"]),
        )
        assert r.status_code == 409

    def test_update_after_start_forbidden(self, client, users, started_competition):
        comp, p = started_competition["comp"], started_competition["p1"]
        r = client.patch(
            f"/competitions/{comp['id']}/participants/{p['id']}",
            json={"comment": "нельзя"},
            headers=auth(users["admin"]["token"]),
        )
        assert r.status_code == 403

    def test_update_by_non_admin_forbidden(self, client, users, competition):
        from tests.conftest import add_judge

        add_judge(client, users["admin"]["token"], users["judge"], competition["id"])
        p = add_participant(client, users["admin"]["token"], competition["id"])
        r = client.patch(
            f"/competitions/{competition['id']}/participants/{p['id']}",
            json={"comment": "нельзя"},
            headers=auth(users["judge"]["token"]),
        )
        assert r.status_code == 403

    def test_update_unknown_participant(self, client, users, competition):
        r = client.patch(
            f"/competitions/{competition['id']}/participants/999999",
            json={"comment": "нет такого"},
            headers=auth(users["admin"]["token"]),
        )
        assert r.status_code == 404

    def test_delete_participant(self, client, users, competition):
        p = add_participant(client, users["admin"]["token"], competition["id"])
        r = client.delete(
            f"/competitions/{competition['id']}/participants/{p['id']}",
            headers=auth(users["admin"]["token"]),
        )
        assert r.status_code == 204
        remaining = client.get(
            f"/competitions/{competition['id']}/participants",
            headers=auth(users["admin"]["token"]),
        ).json()
        assert all(x["id"] != p["id"] for x in remaining)

    def test_delete_after_start_forbidden(self, client, users, started_competition):
        comp, p = started_competition["comp"], started_competition["p1"]
        r = client.delete(
            f"/competitions/{comp['id']}/participants/{p['id']}",
            headers=auth(users["admin"]["token"]),
        )
        assert r.status_code == 403


class TestListAndLimit:
    def test_judge_can_list(self, client, users, competition):
        from tests.conftest import add_judge

        add_judge(client, users["admin"]["token"], users["judge"], competition["id"])
        add_participant(client, users["admin"]["token"], competition["id"])
        r = client.get(
            f"/competitions/{competition['id']}/participants",
            headers=auth(users["judge"]["token"]),
        )
        assert r.status_code == 200
        assert len(r.json()) == 1

    def test_outsider_cannot_list(self, client, users, competition):
        r = client.get(
            f"/competitions/{competition['id']}/participants",
            headers=auth(users["outsider"]["token"]),
        )
        assert r.status_code == 404

    def test_max_participants_limit(self, client, users, competition, monkeypatch):
        from judge_helper import services
        from judge_helper.routers import participants as participants_router

        # Лимит импортирован в роутер по значению — патчим оба места
        monkeypatch.setattr(services, "MAX_PARTICIPANTS", 2)
        monkeypatch.setattr(participants_router, "MAX_PARTICIPANTS", 2)
        cid = competition["id"]
        assert _add(client, users["admin"]["token"], cid, first_name="Первый").status_code == 201
        assert _add(client, users["admin"]["token"], cid, first_name="Второй").status_code == 201
        r = _add(client, users["admin"]["token"], cid, first_name="Третий")
        assert r.status_code == 400
        assert "лимит" in r.json()["detail"]
