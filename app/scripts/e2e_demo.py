#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сквозной демо-сценарий «Помощник судьи» (полный цикл по ТЗ).

Выполняет: регистрация двух судей (с подтверждением email), создание
соревнования, приглашение, участники, старт, оценки (+ офлайн-синхронизация),
завершение, результаты, статистика по судьям, выгрузки CSV/PNG/PDF,
уведомления, настройки, восстановление пароля.

Использование:
    python scripts/e2e_demo.py [BASE_URL]
    BASE_URL по умолчанию: http://127.0.0.1:8000
"""

from __future__ import annotations

import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
RUN = str(int(time.time()))[-6:]  # уникальность email между запусками
client = httpx.Client(base_url=BASE, timeout=30.0)
STEP = 0

ADMIN_EMAIL = f"demo-admin-{RUN}@example.com"
JUDGE_EMAIL = f"demo-judge-{RUN}@example.com"


def ok(label: str, resp: httpx.Response) -> dict:
    global STEP
    STEP += 1
    try:
        body = resp.json()
    except Exception:
        body = {}
    status = "OK" if resp.is_success else "FAIL"
    print(f"[{STEP:02d}] {label}: {status} ({resp.status_code})")
    if not resp.is_success:
        print("     ", body)
        sys.exit(1)
    return body


def main() -> None:
    print(f"Сквозной демо-сценарий «Помощник судьи» → {BASE}\n")

    # 1. Регистрация администратора + подтверждение email
    reg = ok(
        "Регистрация судьи №1 (18+)",
        client.post(
            "/auth/register",
            json={
                "email": ADMIN_EMAIL,
                "full_name": "Демо Администратор Админович",
                "password": "Demo12345",
                "adult": True,
            },
        ),
    )
    ok(
        "Подтверждение email",
        client.post("/auth/confirm-email", json={"token": reg["confirmation_token"]}),
    )
    admin = ok(
        "Вход", client.post("/auth/login", json={"email": ADMIN_EMAIL, "password": "Demo12345"})
    )

    # 2. Регистрация второго судьи
    reg2 = ok(
        "Регистрация судьи №2",
        client.post(
            "/auth/register",
            json={
                "email": JUDGE_EMAIL,
                "full_name": "Демо Судья Судьевич",
                "password": "Demo12345",
                "adult": True,
            },
        ),
    )
    ok(
        "Подтверждение email №2",
        client.post("/auth/confirm-email", json={"token": reg2["confirmation_token"]}),
    )
    judge = ok(
        "Вход №2", client.post("/auth/login", json={"email": JUDGE_EMAIL, "password": "Demo12345"})
    )

    def h(who: dict) -> dict:
        return {"Authorization": "Bearer " + who["access_token"]}

    # 3. Соревнование
    comp = ok(
        "Создание соревнования",
        client.post(
            "/competitions",
            json={"name": "Демо-кубок 2026", "place": "Онлайн", "dates": "20.12.2026"},
            headers=h(admin),
        ),
    )
    cid = comp["id"]

    invite = ok(
        "Приглашение судьи №2",
        client.post(f"/competitions/{cid}/invites", json={"email": JUDGE_EMAIL}, headers=h(admin)),
    )
    ok(
        "Принятие приглашения",
        client.post(f"/competitions/{cid}/invites/{invite['id']}/accept", headers=h(judge)),
    )

    # 4. Участники (две категории)
    p1 = ok(
        "Участник №1",
        client.post(
            f"/competitions/{cid}/participants",
            json={
                "last_name": "Кузнецов",
                "first_name": "Алексей",
                "patronymic": "Сергеевич",
                "category": "Взрослые",
            },
            headers=h(admin),
        ),
    )
    p2 = ok(
        "Участник №2",
        client.post(
            f"/competitions/{cid}/participants",
            json={
                "last_name": "Морозова",
                "first_name": "Анна",
                "patronymic": "Игоревна",
                "category": "Юниоры",
            },
            headers=h(admin),
        ),
    )

    # 5. Старт и оценки (обычная + офлайн-синхронизация)
    ok("Начало соревнования", client.post(f"/competitions/{cid}/start", headers=h(admin)))
    ok(
        "Оценка админа (участник 1)",
        client.put(
            f"/competitions/{cid}/scores/participants/{p1['id']}",
            json={"artistry": 8, "technique": 7, "comment": "Отлично"},
            headers=h(admin),
        ),
    )
    ok(
        "Оценка админа (участник 2)",
        client.put(
            f"/competitions/{cid}/scores/participants/{p2['id']}",
            json={"artistry": 6, "technique": 9},
            headers=h(admin),
        ),
    )
    ok(
        "Оценки судьи №2 (офлайн-синхронизация)",
        client.post(
            f"/competitions/{cid}/scores/sync",
            json={
                "items": [
                    {
                        "participant_id": p1["id"],
                        "artistry": 7,
                        "technique": 8,
                        "comment": "офлайн",
                        "client_time": "2026-12-20T10:00:00",
                    },
                    {
                        "participant_id": p2["id"],
                        "artistry": 9,
                        "technique": 8,
                        "client_time": "2026-12-20T10:01:00",
                    },
                ]
            },
            headers=h(judge),
        ),
    )

    # 6. Завершение и результаты
    ok("Завершение соревнования", client.post(f"/competitions/{cid}/finish", headers=h(admin)))
    results = ok("Результаты", client.get(f"/competitions/{cid}/results", headers=h(judge)))
    for row in results:
        print(
            f"      {row['participant']['last_name']} {row['participant']['first_name']}: "
            f"балл {row['competition_score']}, арт. {row['artistry_score']}, "
            f"тех. {row['technique_score']}"
        )

    # 7. Отчёты
    ok(
        "Статистика по судьям (админ)",
        client.get(f"/competitions/{cid}/judges/stats", headers=h(admin)),
    )
    ok("CSV гистограмма", client.get(f"/competitions/{cid}/reports/chart.csv", headers=h(judge)))
    ok("PNG гистограмма", client.get(f"/competitions/{cid}/reports/chart.png", headers=h(judge)))
    pdf1 = client.get(f"/competitions/{cid}/protocol.pdf", headers=h(judge))
    ok("PDF протокол", pdf1)
    pdf2 = client.get(f"/competitions/{cid}/protocol.pdf", headers=h(judge))
    print(f"      PDF детерминирован: {pdf1.content == pdf2.content} ({len(pdf1.content)} байт)")

    # 8. Уведомления и настройки
    notes = ok("Уведомления судьи №2", client.get("/notifications", headers=h(judge)))
    print(f"      непрочитанных: {sum(1 for n in notes if not n['read'])} из {len(notes)}")
    ok(
        "Настройки уведомлений",
        client.patch("/settings", json={"email_notifications_enabled": True}, headers=h(judge)),
    )

    # 9. Восстановление пароля
    forgot = ok(
        "Запрос сброса пароля", client.post("/auth/forgot-password", json={"email": JUDGE_EMAIL})
    )
    ok(
        "Сброс пароля",
        client.post(
            "/auth/reset-password",
            json={"token": forgot["reset_token"], "new_password": "NewDemo678"},
        ),
    )
    ok(
        "Вход с новым паролем",
        client.post("/auth/login", json={"email": JUDGE_EMAIL, "password": "NewDemo678"}),
    )

    print(f"\nВсе шаги выполнены успешно ({STEP}).")


if __name__ == "__main__":
    main()
