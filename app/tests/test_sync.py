"""Тесты офлайн-синхронизации оценок: LWW, конфликты, отсутствие дублей (SYNC-*)."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from judge_helper.models import AuditLog, Score
from tests.conftest import auth


def _sync(client, token, comp_id, items):
    return client.post(
        f"/competitions/{comp_id}/scores/sync",
        json={"items": items},
        headers=auth(token),
    )


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class TestLWW:
    def test_sync_creates_missing_scores(self, client, users, started_competition):
        comp, p = started_competition["comp"], started_competition["p1"]
        client_time = datetime.utcnow() + timedelta(seconds=5)
        r = _sync(
            client,
            users["judge"]["token"],
            comp["id"],
            [
                {
                    "participant_id": p["id"],
                    "artistry": 8,
                    "technique": 6,
                    "comment": "офлайн",
                    "client_time": _iso(client_time),
                },
            ],
        )
        assert r.status_code == 200
        body = r.json()
        assert body["applied"] == 1 and body["skipped"] == 0

        db = client.app.state.session_factory()
        score = db.execute(
            select(Score).where(
                Score.judge_id == users["judge"]["user"]["id"],
                Score.participant_id == p["id"],
            )
        ).scalar_one()
        db.close()
        assert score.artistry == 8

    def test_newer_client_time_wins(self, client, users, started_competition):
        """LWW: более позднее клиентское время побеждает."""
        comp, p = started_competition["comp"], started_competition["p1"]
        client.put(
            f"/competitions/{comp['id']}/scores/participants/{p['id']}",
            json={"artistry": 3, "technique": 3},
            headers=auth(users["judge"]["token"]),
        )
        future = datetime.utcnow() + timedelta(minutes=10)
        r = _sync(
            client,
            users["judge"]["token"],
            comp["id"],
            [
                {
                    "participant_id": p["id"],
                    "artistry": 9,
                    "technique": 9,
                    "comment": "с другого устройства",
                    "client_time": _iso(future),
                },
            ],
        )
        assert r.status_code == 200
        assert r.json()["applied"] == 1

        db = client.app.state.session_factory()
        scores = (
            db.execute(
                select(Score).where(
                    Score.judge_id == users["judge"]["user"]["id"],
                    Score.participant_id == p["id"],
                )
            )
            .scalars()
            .all()
        )
        db.close()
        assert len(scores) == 1  # дублей нет
        assert scores[0].artistry == 9

        # Уведомление «Оценка была обновлена с другого устройства» (ТЗ)
        notes = client.get("/notifications", headers=auth(users["judge"]["token"])).json()
        assert any("с другого устройства" in n["text"] for n in notes)

    def test_older_client_time_loses_with_conflict_marker(
        self, client, users, started_competition
    ):
        comp, p = started_competition["comp"], started_competition["p1"]
        client.put(
            f"/competitions/{comp['id']}/scores/participants/{p['id']}",
            json={"artistry": 9, "technique": 9},
            headers=auth(users["judge"]["token"]),
        )
        # «Старое» устройство синхронизирует запись с прошедшим временем
        past = datetime.utcnow() - timedelta(minutes=30)
        r = _sync(
            client,
            users["judge"]["token"],
            comp["id"],
            [
                {
                    "participant_id": p["id"],
                    "artistry": 1,
                    "technique": 2,
                    "comment": "проигравшая запись",
                    "client_time": _iso(past),
                },
            ],
        )
        assert r.status_code == 200
        assert r.json()["applied"] == 0 and r.json()["skipped"] == 1
        assert r.json()["results"][0]["reason"] == "older"

        # Оценка не перезаписана
        db = client.app.state.session_factory()
        score = db.execute(
            select(Score).where(
                Score.judge_id == users["judge"]["user"]["id"],
                Score.participant_id == p["id"],
            )
        ).scalar_one()
        assert score.artistry == 9

        # Проигравшая запись — в аудит-логе с пометкой conflict_overwritten (ТЗ)
        conflicts = [
            a
            for a in db.execute(
                select(AuditLog).where(AuditLog.conflict_overwritten.is_(True))
            ).scalars()
        ]
        db.close()
        assert len(conflicts) == 1
        loser = conflicts[0]
        assert json_value(loser.new_value)["artistry"] == 1
        assert loser.action == "conflict"

    def test_equal_client_time_server_reception_wins(self, client, users, started_competition):
        """При равенстве времени — по серверному времени приёма (позже принятая)."""
        comp, p = started_competition["comp"], started_competition["p1"]
        client_time = datetime.utcnow()
        client.put(
            f"/competitions/{comp['id']}/scores/participants/{p['id']}",
            json={"artistry": 5, "technique": 5},
            headers=auth(users["judge"]["token"]),
        )
        # Выравниваем client_time существующей записи с входящей
        db = client.app.state.session_factory()
        score = db.execute(
            select(Score).where(
                Score.judge_id == users["judge"]["user"]["id"],
                Score.participant_id == p["id"],
            )
        ).scalar_one()
        score.client_time = client_time
        db.commit()
        db.close()

        # Тот же client_time пришёл на сервер позже — побеждает по времени приёма
        r = _sync(
            client,
            users["judge"]["token"],
            comp["id"],
            [
                {
                    "participant_id": p["id"],
                    "artistry": 6,
                    "technique": 6,
                    "client_time": _iso(client_time),
                },
            ],
        )
        assert r.json()["applied"] == 1
        db = client.app.state.session_factory()
        score = db.execute(
            select(Score).where(
                Score.judge_id == users["judge"]["user"]["id"],
                Score.participant_id == p["id"],
            )
        ).scalar_one()
        db.close()
        assert score.artistry == 6


def json_value(raw: str | None) -> dict:
    import json

    return json.loads(raw or "{}")


class TestSyncAccess:
    def test_before_start_forbidden(self, client, users, competition):
        r = _sync(
            client,
            users["admin"]["token"],
            competition["id"],
            [
                {
                    "participant_id": 1,
                    "artistry": 1,
                    "technique": 1,
                    "client_time": _iso(datetime.utcnow()),
                },
            ],
        )
        assert r.status_code == 403

    def test_after_finish_forbidden(self, client, users, finished_competition):
        comp = finished_competition["comp"]
        r = _sync(
            client,
            users["admin"]["token"],
            comp["id"],
            [
                {
                    "participant_id": finished_competition["p1"]["id"],
                    "artistry": 1,
                    "technique": 1,
                    "client_time": _iso(datetime.utcnow()),
                },
            ],
        )
        assert r.status_code == 403

    def test_outsider_not_found(self, client, users, started_competition):
        comp = started_competition["comp"]
        r = _sync(
            client,
            users["outsider"]["token"],
            comp["id"],
            [
                {
                    "participant_id": 1,
                    "artistry": 1,
                    "technique": 1,
                    "client_time": _iso(datetime.utcnow()),
                },
            ],
        )
        assert r.status_code == 404

    def test_invalid_values_rejected(self, client, users, started_competition):
        comp, p = started_competition["comp"], started_competition["p1"]
        r = _sync(
            client,
            users["admin"]["token"],
            comp["id"],
            [
                {
                    "participant_id": p["id"],
                    "artistry": 11,
                    "technique": 0,
                    "client_time": _iso(datetime.utcnow()),
                },
            ],
        )
        assert r.status_code == 422
