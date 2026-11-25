"""Общие фикстуры и хелперы для API-тестов."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from judge_helper.main import create_app


@pytest.fixture(autouse=True)
def fast_password_hashing(monkeypatch):
    """Ускорение тестов: меньше итераций PBKDF2 (на безопасность тестов не влияет)."""
    from judge_helper import security

    monkeypatch.setattr(security, "ITERATIONS", 1_000)


@pytest.fixture()
def client() -> TestClient:
    """Клиент с изолированной in-memory базой (каждый тест — чистая БД)."""
    app = create_app(database_url="sqlite+pysqlite:///:memory:")
    with TestClient(app) as c:
        yield c


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def raw_register(
    client: TestClient, email: str, full_name: str, password: str = "Passw0rd!", adult: bool = True
) -> dict:
    """Регистрация без подтверждения email — для тестов самого флоу авторизации."""
    r = client.post(
        "/auth/register",
        json={"email": email, "full_name": full_name, "password": password, "adult": adult},
    )
    assert r.status_code == 201, r.text
    return r.json()


def register_user(
    client: TestClient, email: str, full_name: str, password: str = "Passw0rd!"
) -> dict:
    """Полный цикл: регистрация → подтверждение email (демо-режим) → вход.

    Возвращает {"token": access, "refresh_token": ..., "user": ...}."""
    reg = raw_register(client, email, full_name, password)
    assert reg["confirmation_token"], "ECHO_EMAIL_TOKENS должен быть включён в тестах"
    r = client.post("/auth/confirm-email", json={"token": reg["confirmation_token"]})
    assert r.status_code == 200, r.text
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    body = r.json()
    return {
        "token": body["access_token"],
        "refresh_token": body["refresh_token"],
        "user": body["user"],
    }


def create_competition(
    client: TestClient,
    token: str,
    name: str = "Кубок города",
    place: str = "ДК Железнодорожников",
    dates: str = "20.12.2026",
) -> dict:
    r = client.post(
        "/competitions",
        json={"name": name, "place": place, "dates": dates},
        headers=auth(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def add_participant(
    client: TestClient,
    token: str,
    competition_id: int,
    last_name: str = "Кузнецов",
    first_name: str = "Алексей",
    patronymic: str = "Сергеевич",
    category: str = "Взрослые",
    comment: str = "",
) -> dict:
    r = client.post(
        f"/competitions/{competition_id}/participants",
        json={
            "last_name": last_name,
            "first_name": first_name,
            "patronymic": patronymic,
            "category": category,
            "comment": comment,
        },
        headers=auth(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


def invite_judge(
    client: TestClient, admin_token: str, judge_email: str, competition_id: int
) -> int:
    r = client.post(
        f"/competitions/{competition_id}/invites",
        json={"email": judge_email},
        headers=auth(admin_token),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def accept_invite(client: TestClient, judge_token: str, competition_id: int, invite_id: int):
    r = client.post(
        f"/competitions/{competition_id}/invites/{invite_id}/accept",
        headers=auth(judge_token),
    )
    assert r.status_code == 200, r.text
    return r.json()


def add_judge(client: TestClient, admin_token: str, judge: dict, competition_id: int) -> None:
    """Полный цикл: приглашение администратором + принятие судьёй."""
    invite_id = invite_judge(client, admin_token, judge["user"]["email"], competition_id)
    accept_invite(client, judge["token"], competition_id, invite_id)


def start_competition(client: TestClient, token: str, competition_id: int) -> dict:
    r = client.post(f"/competitions/{competition_id}/start", headers=auth(token))
    assert r.status_code == 200, r.text
    return r.json()


def save_score(
    client: TestClient,
    token: str,
    competition_id: int,
    participant_id: int,
    artistry: int,
    technique: int,
    comment: str = "",
):
    return client.put(
        f"/competitions/{competition_id}/scores/participants/{participant_id}",
        json={"artistry": artistry, "technique": technique, "comment": comment},
        headers=auth(token),
    )


def finish_competition(client: TestClient, token: str, competition_id: int):
    return client.post(f"/competitions/{competition_id}/finish", headers=auth(token))


@pytest.fixture()
def users(client) -> dict:
    """Три пользователя: администратор соревнования, второй судья, посторонний."""
    return {
        "admin": register_user(client, "admin@example.com", "Иванов Иван Иванович"),
        "judge": register_user(client, "judge@example.com", "Петров Пётр Петрович"),
        "outsider": register_user(client, "other@example.com", "Сидоров Сидор Сидорович"),
    }


@pytest.fixture()
def competition(client, users) -> dict:
    """Соревнование, созданное администратором (не начато)."""
    return create_competition(client, users["admin"]["token"])


@pytest.fixture()
def started_competition(client, users, competition) -> dict:
    """Начатое соревнование: 2 судьи (админ + приглашённый), 2 участника."""
    add_judge(client, users["admin"]["token"], users["judge"], competition["id"])
    p1 = add_participant(
        client,
        users["admin"]["token"],
        competition["id"],
        last_name="Кузнецов",
        first_name="Алексей",
        patronymic="Сергеевич",
        category="Взрослые",
    )
    p2 = add_participant(
        client,
        users["admin"]["token"],
        competition["id"],
        last_name="Морозова",
        first_name="Анна",
        patronymic="Игоревна",
        category="Юниоры",
    )
    start_competition(client, users["admin"]["token"], competition["id"])
    return {"comp": competition, "p1": p1, "p2": p2}


@pytest.fixture()
def finished_competition(client, users, started_competition) -> dict:
    """Завершённое соревнование: оба участника оценены обоими судьями."""
    comp = started_competition["comp"]
    for p in (started_competition["p1"], started_competition["p2"]):
        save_score(client, users["admin"]["token"], comp["id"], p["id"], 5, 5)
        save_score(client, users["judge"]["token"], comp["id"], p["id"], 7, 3)
    r = finish_competition(client, users["admin"]["token"], comp["id"])
    assert r.status_code == 200, r.text
    return started_competition
