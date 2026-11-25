"""Тесты уведомлений и настроек (NOTIF-* кейсы ТЗ)."""

from __future__ import annotations

from sqlalchemy import select

from judge_helper.models import EmailOutbox
from tests.conftest import auth, invite_judge


def _notifications(client, token):
    r = client.get("/notifications", headers=auth(token))
    assert r.status_code == 200
    return r.json()


def _outbox(client):
    """Email-дубликаты уведомлений (письма регистрации не учитываем)."""
    db = client.app.state.session_factory()
    rows = [
        r
        for r in db.execute(select(EmailOutbox)).scalars()
        if r.subject == "Помощник судьи: уведомление"
    ]
    db.close()
    return rows


class TestSettings:
    def test_defaults(self, client, users):
        r = client.get("/settings", headers=auth(users["judge"]["token"]))
        assert r.status_code == 200
        body = r.json()
        assert body["notifications_enabled"] is True
        assert body["email_notifications_enabled"] is False

    def test_update_settings(self, client, users):
        r = client.patch(
            "/settings",
            headers=auth(users["judge"]["token"]),
            json={"email_notifications_enabled": True},
        )
        assert r.status_code == 200
        assert r.json()["email_notifications_enabled"] is True


class TestNotificationEvents:
    def test_invite_notification_for_existing_user(self, client, users, competition):
        invite_judge(
            client, users["admin"]["token"], users["judge"]["user"]["email"], competition["id"]
        )
        notes = _notifications(client, users["judge"]["token"])
        assert any(n["kind"] == "invited" for n in notes)

    def test_accept_notifies_admin(self, client, users, competition):
        from tests.conftest import add_judge

        add_judge(client, users["admin"]["token"], users["judge"], competition["id"])
        notes = _notifications(client, users["admin"]["token"])
        assert any(n["kind"] == "invite_status" and "принял" in n["text"] for n in notes)

    def test_decline_notifies_admin(self, client, users, competition):
        invite_id = invite_judge(
            client, users["admin"]["token"], users["judge"]["user"]["email"], competition["id"]
        )
        client.post(
            f"/competitions/{competition['id']}/invites/{invite_id}/decline",
            headers=auth(users["judge"]["token"]),
        )
        notes = _notifications(client, users["admin"]["token"])
        assert any("отклонил" in n["text"] for n in notes)

    def test_removed_judge_notified(self, client, users, competition):
        from tests.conftest import add_judge

        add_judge(client, users["admin"]["token"], users["judge"], competition["id"])
        client.delete(
            f"/competitions/{competition['id']}/judges/{users['judge']['user']['id']}",
            headers=auth(users["admin"]["token"]),
        )
        notes = _notifications(client, users["judge"]["token"])
        assert any(n["kind"] == "removed" for n in notes)

    def test_start_and_finish_notify_all_judges(self, client, users, started_competition):
        comp = started_competition["comp"]
        for who in (users["admin"], users["judge"]):
            notes = _notifications(client, who["token"])
            assert any(n["kind"] == "started" for n in notes)
            assert not any(n["kind"] == "finished" for n in notes)

        for p in (started_competition["p1"], started_competition["p2"]):
            client.put(
                f"/competitions/{comp['id']}/scores/participants/{p['id']}",
                json={"artistry": 5, "technique": 5},
                headers=auth(users["admin"]["token"]),
            )
        client.post(f"/competitions/{comp['id']}/finish", headers=auth(users["admin"]["token"]))

        for who in (users["admin"], users["judge"]):
            notes = _notifications(client, who["token"])
            assert any(n["kind"] == "finished" for n in notes)

    def test_manual_delete_notifies_judges(self, client, users, competition):
        from tests.conftest import add_judge

        add_judge(client, users["admin"]["token"], users["judge"], competition["id"])
        client.delete(f"/competitions/{competition['id']}", headers=auth(users["admin"]["token"]))
        notes = _notifications(client, users["judge"]["token"])
        assert any(n["kind"] == "deleted" for n in notes)


class TestEmailDuplicates:
    def test_email_sent_only_when_enabled(self, client, users, competition):
        from tests.conftest import add_judge

        # По умолчанию email-дубликаты выключены
        add_judge(client, users["admin"]["token"], users["judge"], competition["id"])
        assert _notifications(client, users["judge"]["token"])
        assert _outbox(client) == []

        # Включаем — появляется email-дубликат
        client.patch(
            "/settings",
            headers=auth(users["judge"]["token"]),
            json={"email_notifications_enabled": True},
        )
        client.delete(f"/competitions/{competition['id']}", headers=auth(users["admin"]["token"]))
        outbox = _outbox(client)
        assert any("удалено" in m.body for m in outbox)

    def test_notifications_disabled_stops_everything(self, client, users, competition):
        client.patch(
            "/settings",
            headers=auth(users["judge"]["token"]),
            json={"notifications_enabled": False},
        )
        invite_judge(
            client, users["admin"]["token"], users["judge"]["user"]["email"], competition["id"]
        )
        assert _notifications(client, users["judge"]["token"]) == []
        assert _outbox(client) == []


class TestNotificationListing:
    def test_list_and_mark_read(self, client, users, competition):
        invite_judge(
            client, users["admin"]["token"], users["judge"]["user"]["email"], competition["id"]
        )
        notes = _notifications(client, users["judge"]["token"])
        assert len(notes) == 1 and notes[0]["read"] is False

        r = client.get("/notifications?unread_only=true", headers=auth(users["judge"]["token"]))
        assert len(r.json()) == 1

        r = client.post("/notifications/read-all", headers=auth(users["judge"]["token"]))
        assert r.status_code == 204
        r = client.get("/notifications?unread_only=true", headers=auth(users["judge"]["token"]))
        assert r.json() == []
