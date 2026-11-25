"""Тесты аудит-лога (AUDIT-* кейсы ТЗ): поля, действия, retention."""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select

from judge_helper.models import AuditLog, utcnow
from tests.conftest import add_participant, auth


def _entries(client, **filters):
    db = client.app.state.session_factory()
    query = select(AuditLog)
    for key, value in filters.items():
        query = query.where(getattr(AuditLog, key) == value)
    rows = list(db.execute(query).scalars())
    db.close()
    return rows


class TestAuditFields:
    def test_competition_create_logged(self, client, users, competition):
        entries = _entries(client, action="create", entity_type="competition")
        assert len(entries) == 1
        entry = entries[0]
        assert entry.user_id == users["admin"]["user"]["id"]
        assert entry.role == "admin"
        # Технические данные запроса зафиксированы
        assert entry.ip
        assert entry.user_agent
        assert entry.request_id
        assert json.loads(entry.new_value)["name"] == "Кубок города"
        assert entry.conflict_overwritten is False

    def test_score_save_logs_old_and_new(self, client, users, started_competition):
        comp, p = started_competition["comp"], started_competition["p1"]
        client.put(
            f"/competitions/{comp['id']}/scores/participants/{p['id']}",
            json={"artistry": 5, "technique": 5, "comment": "первая"},
            headers=auth(users["admin"]["token"]),
        )
        client.put(
            f"/competitions/{comp['id']}/scores/participants/{p['id']}",
            json={"artistry": 7, "technique": 6, "comment": "вторая"},
            headers=auth(users["admin"]["token"]),
        )
        updates = _entries(client, action="update", entity_type="score")
        assert len(updates) == 1
        old = json.loads(updates[0].old_value)
        new = json.loads(updates[0].new_value)
        # Для оценок фиксируются старый и новый балл, комментарий (ТЗ)
        assert old["artistry"] == 5 and old["comment"] == "первая"
        assert new["artistry"] == 7 and new["comment"] == "вторая"

    def test_participant_actions_logged(self, client, users, competition):
        p = add_participant(client, users["admin"]["token"], competition["id"])
        client.patch(
            f"/competitions/{competition['id']}/participants/{p['id']}",
            json={"category": "Ветераны"},
            headers=auth(users["admin"]["token"]),
        )
        client.delete(
            f"/competitions/{competition['id']}/participants/{p['id']}",
            headers=auth(users["admin"]["token"]),
        )
        assert _entries(client, action="create", entity_type="participant")
        assert _entries(client, action="update", entity_type="participant")
        assert _entries(client, action="delete", entity_type="participant")

    def test_lifecycle_actions_logged(self, client, users, started_competition):
        comp = started_competition["comp"]
        assert _entries(client, action="start", entity_type="competition")
        for p in (started_competition["p1"], started_competition["p2"]):
            client.put(
                f"/competitions/{comp['id']}/scores/participants/{p['id']}",
                json={"artistry": 1, "technique": 1},
                headers=auth(users["admin"]["token"]),
            )
        client.post(f"/competitions/{comp['id']}/finish", headers=auth(users["admin"]["token"]))
        assert _entries(client, action="finish", entity_type="competition")

    def test_invite_actions_logged(self, client, users, competition):
        from tests.conftest import accept_invite, invite_judge

        invite_id = invite_judge(
            client, users["admin"]["token"], users["judge"]["user"]["email"], competition["id"]
        )
        accept_invite(client, users["judge"]["token"], competition["id"], invite_id)
        assert _entries(client, action="invite", entity_type="invite")
        assert _entries(client, action="accept", entity_type="invite")


class TestAuditRetention:
    def test_old_rows_purged(self, client, users, competition):
        db = client.app.state.session_factory()
        db.add(
            AuditLog(
                ts=utcnow() - timedelta(days=200),
                user_id=1,
                role="judge",
                action="create",
                entity_type="participant",
                entity_id="1",
            )
        )
        db.commit()
        db.close()

        # sweep вызывается лениво при запросе списка соревнований
        client.get("/competitions", headers=auth(users["admin"]["token"]))

        remaining = _entries(client, entity_type="participant", action="create")
        # Старая запись удалена (хранение — 6 месяцев), новая осталась
        assert all(e.ts > utcnow() - timedelta(days=190) for e in remaining)
