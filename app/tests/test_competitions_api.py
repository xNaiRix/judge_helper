"""API-тесты соревнований: жизненный цикл, права, приглашения (COMP-* кейсы)."""

from __future__ import annotations

from datetime import timedelta

from judge_helper.models import Invite, utcnow
from tests.conftest import (
    accept_invite,
    add_judge,
    auth,
    create_competition,
    invite_judge,
    save_score,
    start_competition,
)


class TestCreateAndList:
    def test_create_competition(self, client, users, competition):
        assert competition["status"] == "planned"
        assert competition["is_admin"] is True
        assert competition["name"] == "Кубок города"

    def test_creator_becomes_judge(self, client, users, competition):
        r = client.get(
            f"/competitions/{competition['id']}/judges", headers=auth(users["admin"]["token"])
        )
        assert r.status_code == 200
        judges = r.json()
        assert len(judges) == 1
        assert judges[0]["email"] == users["admin"]["user"]["email"]

    def test_list_shows_only_my_competitions(self, client, users, competition):
        create_competition(client, users["outsider"]["token"], name="Чужое соревнование")
        r = client.get("/competitions", headers=auth(users["admin"]["token"]))
        assert r.status_code == 200
        names = [c["name"] for c in r.json()]
        assert names == ["Кубок города"]
        assert all(c["is_admin"] for c in r.json())

    def test_outsider_cannot_open_competition(self, client, users, competition):
        # IDOR: чужим соревнование не раскрывается
        r = client.get(
            f"/competitions/{competition['id']}", headers=auth(users["outsider"]["token"])
        )
        assert r.status_code == 404

    def test_detail_contains_judges(self, client, users, competition):
        add_judge(client, users["admin"]["token"], users["judge"], competition["id"])
        r = client.get(f"/competitions/{competition['id']}", headers=auth(users["judge"]["token"]))
        assert r.status_code == 200
        body = r.json()
        assert body["is_admin"] is False
        assert len(body["judges"]) == 2

    def test_no_token_rejected(self, client, competition):
        assert client.get("/competitions").status_code == 401


class TestDelete:
    def test_delete_before_start(self, client, users, competition):
        r = client.delete(
            f"/competitions/{competition['id']}", headers=auth(users["admin"]["token"])
        )
        assert r.status_code == 204
        # Удалённое не отображается в списке и по прямой ссылке
        assert client.get("/competitions", headers=auth(users["admin"]["token"])).json() == []
        r = client.get(f"/competitions/{competition['id']}", headers=auth(users["admin"]["token"]))
        assert r.status_code == 404

    def test_delete_by_non_admin_forbidden(self, client, users, competition):
        add_judge(client, users["admin"]["token"], users["judge"], competition["id"])
        r = client.delete(
            f"/competitions/{competition['id']}", headers=auth(users["judge"]["token"])
        )
        assert r.status_code == 403

    def test_delete_during_competition_forbidden(self, client, users, started_competition):
        comp = started_competition["comp"]
        r = client.delete(f"/competitions/{comp['id']}", headers=auth(users["admin"]["token"]))
        assert r.status_code == 403

    def test_delete_after_finish_allowed(self, client, users, started_competition):
        comp = started_competition["comp"]
        for p in (started_competition["p1"], started_competition["p2"]):
            r = save_score(client, users["admin"]["token"], comp["id"], p["id"], 5, 5)
            assert r.status_code == 200
        assert (
            client.post(
                f"/competitions/{comp['id']}/finish", headers=auth(users["admin"]["token"])
            ).status_code
            == 200
        )
        r = client.delete(f"/competitions/{comp['id']}", headers=auth(users["admin"]["token"]))
        assert r.status_code == 204


class TestStartFinish:
    def test_start_by_non_admin_forbidden(self, client, users, competition):
        add_judge(client, users["admin"]["token"], users["judge"], competition["id"])
        r = client.post(
            f"/competitions/{competition['id']}/start", headers=auth(users["judge"]["token"])
        )
        assert r.status_code == 403

    def test_start_twice_conflict(self, client, users, started_competition):
        comp = started_competition["comp"]
        r = client.post(f"/competitions/{comp['id']}/start", headers=auth(users["admin"]["token"]))
        assert r.status_code == 409

    def test_finish_blocked_when_unmarked_participants(self, client, users, started_competition):
        comp = started_competition["comp"]
        # Оцениваем только первого участника
        save_score(
            client, users["admin"]["token"], comp["id"], started_competition["p1"]["id"], 5, 5
        )
        r = client.post(
            f"/competitions/{comp['id']}/finish", headers=auth(users["admin"]["token"])
        )
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["participants"] == ["Морозова Анна Игоревна"]

    def test_finish_before_start_forbidden(self, client, users, competition):
        r = client.post(
            f"/competitions/{competition['id']}/finish", headers=auth(users["admin"]["token"])
        )
        assert r.status_code == 403

    def test_finish_by_non_admin_forbidden(self, client, users, started_competition):
        comp = started_competition["comp"]
        r = client.post(
            f"/competitions/{comp['id']}/finish", headers=auth(users["judge"]["token"])
        )
        assert r.status_code == 403

    def test_finish_ok_with_warning(self, client, users, started_competition):
        comp = started_competition["comp"]
        # Оба участника оценены администратором, второй судья никого не оценил
        for p in (started_competition["p1"], started_competition["p2"]):
            save_score(client, users["admin"]["token"], comp["id"], p["id"], 5, 5)
        r = client.post(
            f"/competitions/{comp['id']}/finish", headers=auth(users["admin"]["token"])
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "finished"
        assert body["unmarked_participants"] == []
        # Неблокирующее предупреждение о неоценивших судьях (ТЗ)
        assert len(body["warnings"]) == 1
        assert "Петров Пётр Петрович" in body["warnings"][0]


class TestJudgesManagement:
    def test_remove_judge_before_start(self, client, users, competition):
        add_judge(client, users["admin"]["token"], users["judge"], competition["id"])
        judge_id = users["judge"]["user"]["id"]
        r = client.delete(
            f"/competitions/{competition['id']}/judges/{judge_id}",
            headers=auth(users["admin"]["token"]),
        )
        assert r.status_code == 204
        # Доступ удалённого судьи закрывается
        r = client.get(f"/competitions/{competition['id']}", headers=auth(users["judge"]["token"]))
        assert r.status_code == 404

    def test_admin_cannot_remove_himself(self, client, users, competition):
        admin_id = users["admin"]["user"]["id"]
        r = client.delete(
            f"/competitions/{competition['id']}/judges/{admin_id}",
            headers=auth(users["admin"]["token"]),
        )
        assert r.status_code == 403

    def test_remove_judge_after_start_forbidden(self, client, users, started_competition):
        comp = started_competition["comp"]
        r = client.delete(
            f"/competitions/{comp['id']}/judges/{users['judge']['user']['id']}",
            headers=auth(users["admin"]["token"]),
        )
        assert r.status_code == 403

    def test_remove_non_judge(self, client, users, competition):
        outsider_id = users["outsider"]["user"]["id"]
        r = client.delete(
            f"/competitions/{competition['id']}/judges/{outsider_id}",
            headers=auth(users["admin"]["token"]),
        )
        assert r.status_code == 404

    def test_list_judges_by_member(self, client, users, competition):
        add_judge(client, users["admin"]["token"], users["judge"], competition["id"])
        r = client.get(
            f"/competitions/{competition['id']}/judges", headers=auth(users["judge"]["token"])
        )
        assert r.status_code == 200
        assert len(r.json()) == 2


class TestInvites:
    def test_invite_and_accept(self, client, users, competition):
        invite_id = invite_judge(
            client, users["admin"]["token"], users["judge"]["user"]["email"], competition["id"]
        )
        body = accept_invite(client, users["judge"]["token"], competition["id"], invite_id)
        assert body["status"] == "accepted"
        # Судья появился в списке судей
        judges = client.get(
            f"/competitions/{competition['id']}/judges", headers=auth(users["admin"]["token"])
        ).json()
        assert any(j["email"] == users["judge"]["user"]["email"] for j in judges)

    def test_invite_by_non_admin_forbidden(self, client, users, competition):
        add_judge(client, users["admin"]["token"], users["judge"], competition["id"])
        r = client.post(
            f"/competitions/{competition['id']}/invites",
            json={"email": "someone@example.com"},
            headers=auth(users["judge"]["token"]),
        )
        assert r.status_code == 403

    def test_invite_after_start_forbidden(self, client, users, started_competition):
        comp = started_competition["comp"]
        r = client.post(
            f"/competitions/{comp['id']}/invites",
            json={"email": "someone@example.com"},
            headers=auth(users["admin"]["token"]),
        )
        assert r.status_code == 403

    def test_accept_after_start_expired(self, client, users, competition):
        # Создаём приглашение до старта
        invite_id = invite_judge(
            client, users["admin"]["token"], users["judge"]["user"]["email"], competition["id"]
        )
        start_competition(client, users["admin"]["token"], competition["id"])
        r = client.post(
            f"/competitions/{competition['id']}/invites/{invite_id}/accept",
            headers=auth(users["judge"]["token"]),
        )
        assert r.status_code == 410

    def test_accept_expired_by_ttl(self, client, users, competition):
        invite_id = invite_judge(
            client, users["admin"]["token"], users["judge"]["user"]["email"], competition["id"]
        )
        # Имитируем истечение недели
        db = client.app.state.session_factory()
        invite = db.get(Invite, invite_id)
        invite.expires_at = utcnow() - timedelta(days=8)
        db.commit()
        db.close()
        r = client.post(
            f"/competitions/{competition['id']}/invites/{invite_id}/accept",
            headers=auth(users["judge"]["token"]),
        )
        assert r.status_code == 410

    def test_accept_by_wrong_user_forbidden(self, client, users, competition):
        invite_id = invite_judge(
            client, users["admin"]["token"], users["judge"]["user"]["email"], competition["id"]
        )
        r = client.post(
            f"/competitions/{competition['id']}/invites/{invite_id}/accept",
            headers=auth(users["outsider"]["token"]),
        )
        assert r.status_code == 403

    def test_accept_twice_conflict(self, client, users, competition):
        invite_id = invite_judge(
            client, users["admin"]["token"], users["judge"]["user"]["email"], competition["id"]
        )
        assert (
            accept_invite(client, users["judge"]["token"], competition["id"], invite_id)["status"]
            == "accepted"
        )
        r = client.post(
            f"/competitions/{competition['id']}/invites/{invite_id}/accept",
            headers=auth(users["judge"]["token"]),
        )
        assert r.status_code == 409

    def test_decline(self, client, users, competition):
        invite_id = invite_judge(
            client, users["admin"]["token"], users["judge"]["user"]["email"], competition["id"]
        )
        r = client.post(
            f"/competitions/{competition['id']}/invites/{invite_id}/decline",
            headers=auth(users["judge"]["token"]),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "declined"
        # Отклонивший не стал судьёй
        judges = client.get(
            f"/competitions/{competition['id']}/judges", headers=auth(users["admin"]["token"])
        ).json()
        assert all(j["email"] != users["judge"]["user"]["email"] for j in judges)

    def test_invite_list_for_admin(self, client, users, competition):
        invite_judge(client, users["admin"]["token"], "new@example.com", competition["id"])
        r = client.get(
            f"/competitions/{competition['id']}/invites", headers=auth(users["admin"]["token"])
        )
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["status"] == "pending"

    def test_invite_list_for_non_admin_forbidden(self, client, users, competition):
        add_judge(client, users["admin"]["token"], users["judge"], competition["id"])
        r = client.get(
            f"/competitions/{competition['id']}/invites", headers=auth(users["judge"]["token"])
        )
        assert r.status_code == 403

    def test_invite_invalid_email(self, client, users, competition):
        r = client.post(
            f"/competitions/{competition['id']}/invites",
            json={"email": "not-an-email"},
            headers=auth(users["admin"]["token"]),
        )
        assert r.status_code == 422

    def test_my_invitations_listing(self, client, users, competition):
        """Список приглашений адресата: данные соревнования и администратора (ТЗ)."""
        invite_id = invite_judge(
            client, users["admin"]["token"], users["judge"]["user"]["email"], competition["id"]
        )

        # У постороннего приглашений нет
        assert client.get("/invitations", headers=auth(users["outsider"]["token"])).json() == []

        r = client.get("/invitations", headers=auth(users["judge"]["token"]))
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 1
        item = items[0]
        assert item["invite_id"] == invite_id
        assert item["competition_id"] == competition["id"]
        assert item["competition_name"] == "Кубок города"
        assert item["dates"] == "20.12.2026"
        assert item["admin_email"] == users["admin"]["user"]["email"]
        assert item["admin_name"] == "Иванов Иван Иванович"

        # После принятия приглашение исчезает из списка
        accept_invite(client, users["judge"]["token"], competition["id"], invite_id)
        assert client.get("/invitations", headers=auth(users["judge"]["token"])).json() == []

    def test_no_invite_to_existing_judge(self, client, users, competition):
        """Судье, уже состоящему в соревновании, приглашение не отправляется."""
        from tests.conftest import add_judge

        add_judge(client, users["admin"]["token"], users["judge"], competition["id"])
        r = client.post(
            f"/competitions/{competition['id']}/invites",
            json={"email": users["judge"]["user"]["email"]},
            headers=auth(users["admin"]["token"]),
        )
        assert r.status_code == 409
        assert "уже является судьёй" in r.json()["detail"]

    def test_no_duplicate_pending_invite(self, client, users, competition):
        """Пока приглашение не обработано, повторное отправить нельзя."""
        invite_judge(
            client, users["admin"]["token"], users["judge"]["user"]["email"], competition["id"]
        )
        r = client.post(
            f"/competitions/{competition['id']}/invites",
            json={"email": users["judge"]["user"]["email"]},
            headers=auth(users["admin"]["token"]),
        )
        assert r.status_code == 409
        assert "уже отправлено" in r.json()["detail"]

    def test_can_invite_again_after_decline(self, client, users, competition):
        """После отклонения приглашения можно отправить новое."""
        invite_id = invite_judge(
            client, users["admin"]["token"], users["judge"]["user"]["email"], competition["id"]
        )
        client.post(
            f"/competitions/{competition['id']}/invites/{invite_id}/decline",
            headers=auth(users["judge"]["token"]),
        )
        r = client.post(
            f"/competitions/{competition['id']}/invites",
            json={"email": users["judge"]["user"]["email"]},
            headers=auth(users["admin"]["token"]),
        )
        assert r.status_code == 201

    def test_can_invite_again_after_removal(self, client, users, competition):
        """После удаления судьи из соревнования его можно пригласить снова."""
        from tests.conftest import add_judge

        add_judge(client, users["admin"]["token"], users["judge"], competition["id"])
        # Пока состоит — нельзя
        r = client.post(
            f"/competitions/{competition['id']}/invites",
            json={"email": users["judge"]["user"]["email"]},
            headers=auth(users["admin"]["token"]),
        )
        assert r.status_code == 409
        # Удаляем из соревнования — снова можно
        client.delete(
            f"/competitions/{competition['id']}/judges/{users['judge']['user']['id']}",
            headers=auth(users["admin"]["token"]),
        )
        r = client.post(
            f"/competitions/{competition['id']}/invites",
            json={"email": users["judge"]["user"]["email"]},
            headers=auth(users["admin"]["token"]),
        )
        assert r.status_code == 201
