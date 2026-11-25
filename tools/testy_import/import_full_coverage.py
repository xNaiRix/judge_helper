#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Дополнение TestY до полного покрытия планом тестирования и распределением задач.

Часть 1. Кейсы, которых не было в табличных разделах плана:
  * 9.6 Локализация (список требований);
  * 9.8 Поддерживаемость (список);
  * 9.9 Правовые (список);
  * раздел 10 — UAT-сценарии;
  * раздел 8 — обязательные проверки матрицы прав доступа.

Часть 2. Пользователи команды (из docs/распределение_задач.xlsx) и план
«Функциональное тестирование — распределение задач (декабрь 2026)»:
  * все 196 функциональных кейсов раздела 7;
  * исполнители (assignee) назначаются на тесты по колонке «Исполнитель» xlsx;
  * даты плана — по колонкам «Создана»/«Дедлайн» xlsx.

Скрипт идемпотентен: повторный запуск не дублирует кейсы/пользователей/план.

Использование:
    python tools/testy_import/import_full_coverage.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dump_xlsx  # noqa: E402
from import_test_cases import TestyAPI  # noqa: E402

XLSX = Path(__file__).resolve().parents[2] / "docs" / "распределение_задач.xlsx"

USER_PASSWORD = "Testy-2026!"  # верхний+нижний регистр, цифра, спецсимвол
TEAM = {
    "Ирина Глубокая": ("irina", "Ирина", "Глубокая", "irina@example.com"),
    "Дарья Силова": ("darya", "Дарья", "Силова", "darya@example.com"),
    "Тимур Тухватуллин": ("timur", "Тимур", "Тухватуллин", "timur@example.com"),
}

PLAN_NAME = "Функциональное тестирование — распределение задач (декабрь 2026)"

TAG_COLORS_EXTRA = {"UAT": "#e65100", "Maintainability": "#0277bd"}

# ---------------------------------------------------------------------------
# Часть 1: недостающие кейсы
# ---------------------------------------------------------------------------

EXTRA_SUITES: dict[str, list[dict]] = {
    # parent_title, suite_name, rows
    ("Раздел 9 — NFR-тесты", "9.6 Локализация"): [
        (
            "LOC-01",
            "Русский язык интерфейса",
            "Пройти ключевые сценарии приложения и проверить язык всех экранов, "
            "сообщений и ошибок.",
            "Интерфейс полностью на русском языке.",
            "P1",
            "Localization",
        ),
        (
            "LOC-02",
            "Формат дат ДД.ММ.ГГГГ",
            "Проверить отображение дат во всех разделах приложения.",
            "Все даты отображаются в формате ДД.ММ.ГГГГ.",
            "P1",
            "Localization",
        ),
        (
            "LOC-03",
            "Часовые пояса",
            "Установить на устройстве часовой пояс, отличный от UTC. "
            "Проверить даты в приложении и в PDF-протоколе.",
            "Даты хранятся в UTC, отображаются в системном поясе устройства; "
            "в PDF указан часовой пояс на момент генерации.",
            "P1",
            "Localization",
        ),
        (
            "LOC-04",
            "Кириллица в PDF/CSV/PNG",
            "Сформировать PDF-протокол, CSV и PNG-графики для данных с кириллицей "
            "(«ё», дефисы, апострофы).",
            "Кириллица отображается корректно во всех выгрузках.",
            "P1",
            "Localization",
        ),
        (
            "LOC-05",
            "Нет обрезания в списке",
            "Открыть список соревнований с длинными названиями (до 200 символов).",
            "Критичная информация не обрезается; отображение корректно.",
            "P1",
            "Localization",
        ),
    ],
    ("Раздел 9 — NFR-тесты", "9.8 Поддерживаемость"): [
        (
            "MAINT-01",
            "CI/CD: автосборка и автотесты",
            "Проверить конвейер GitLab CI: автоматическая сборка и автотесты "
            "запускаются на каждый коммит/по расписанию.",
            "Сборка и тесты выполняются автоматически; pipeline зелёный.",
            "P1",
            "Maintainability",
        ),
        (
            "MAINT-02",
            "Покрытие критических модулей ≥80%",
            "Снять отчёт покрытия критических модулей тестами.",
            "Покрытие критических модулей ≥ 80%.",
            "P1",
            "Maintainability",
        ),
        (
            "MAINT-03",
            "RELEASE-логи без ПД",
            "Проверить RELEASE-логи production-сборки.",
            "Логи не содержат ПД; хранение 30 дней; DEBUG-логи только dev/staging.",
            "P0",
            "Maintainability",
        ),
        (
            "MAINT-04",
            "Алёрты настроены",
            "Проверить настройку алёртов мониторинга.",
            "Алёрты настроены и доходят до ответственных.",
            "P1",
            "Maintainability",
        ),
    ],
    ("Раздел 9 — NFR-тесты", "9.9 Правовые"): [
        (
            "LEGAL-01",
            "152-ФЗ, серверы РФ",
            "Проверить размещение и хранение персональных данных.",
            "ПД хранятся на серверах в РФ; соответствие 152-ФЗ.",
            "P0",
            "Legal",
        ),
        (
            "LEGAL-02",
            "Согласие с сроками хранения",
            "Пройти регистрацию и проверить текст согласия на обработку ПД.",
            "В согласии указаны минимальный срок хранения и срок удаления данных.",
            "P0",
            "Legal",
        ),
        (
            "LEGAL-03",
            "Соглашение и политика",
            "Проверить доступность пользовательского соглашения и политики конфиденциальности.",
            "Документы доступны, актуальны, принимаются при регистрации.",
            "P0",
            "Legal",
        ),
        (
            "LEGAL-04",
            "Совершеннолетие судьи",
            "Проверить обязательность чекбокса 18+ при регистрации (email и Google).",
            "Без подтверждения 18+ регистрация невозможна (см. AUTH-19, AUTH-20).",
            "P0",
            "Legal",
        ),
        (
            "LEGAL-05",
            "Google OAuth — feature flag",
            "Проверить отключение Google OAuth конфигурацией без релиза приложения.",
            "Кнопка скрыта, API возвращает 404/403 (см. AUTH-06).",
            "P0",
            "Legal",
        ),
    ],
    ("Раздел 8 — Матрица прав доступа", "Матрица прав доступа (раздел 8)"): [
        (
            "AM-01",
            "IDOR: прямой API-запрос с чужим ID",
            "Выполнить прямые API-запросы к чужим соревнованиям, участникам, "
            "оценкам, PDF и графикам.",
            "Возврат 403/404; данные не раскрываются.",
            "P0",
            "Access",
        ),
        (
            "AM-02",
            "Подмена роли в токене",
            "Сформировать JWT с подменённой ролью (судья → администратор) и выполнить запросы.",
            "Запросы отклоняются; подпись/срок/алгоритм проверяются.",
            "P0",
            "Security",
        ),
        (
            "AM-03",
            "Повторное использование ссылки приглашения",
            "Принять приглашение и повторно перейти по той же ссылке.",
            "Повторное принятие невозможно.",
            "P0",
            "Access",
        ),
        (
            "AM-04",
            "Доступ не участника к PDF/графикам/списку",
            "Запросить PDF, графики и список судей соревнования, в котором "
            "пользователь не участвует.",
            "Доступ запрещён (403/404).",
            "P0",
            "Access",
        ),
        (
            "AM-05",
            "Удаление себя администратором",
            "Администратору попытаться удалить себя из судей соревнования (UI и API).",
            "Удаление запрещено (UI скрывает, API 403).",
            "P0",
            "Access",
        ),
        (
            "AM-06",
            "Доступ к удалённому соревнованию",
            "Обратиться к удалённому (soft-delete) соревнованию по прямой ссылке и через API.",
            "404/410; данные не раскрываются.",
            "P0",
            "Access",
        ),
    ],
    ("Раздел 10 — UAT и приёмка", "UAT-сценарии (раздел 10)"): [
        (
            "UAT-01",
            "Регистрация с 18+ (10 судей)",
            "Каждый UAT-судья регистрируется по email и через Google с подтверждением 18+.",
            "Регистрация успешна у всех; без 18+ невозможна.",
            "P0",
            "UAT",
        ),
        (
            "UAT-02",
            "Список соревнований и статусы",
            "Судьи открывают меню соревнований и проверяют поля и статусы.",
            "Отображаются название, даты, место, статус; чужих и удалённых нет.",
            "P0",
            "UAT",
        ),
        (
            "UAT-03",
            "Создание соревнования и приглашение",
            "Создать соревнование, пригласить судей по ФИО и по email.",
            "Соревнование создано; приглашения доставлены и отображаются "
            "с полным составом данных.",
            "P0",
            "UAT",
        ),
        (
            "UAT-04",
            "Добавление участников",
            "Администратор добавляет карточки участников во всех категориях.",
            "Карточки создаются; дубли по ФИО (нормализация) отклоняются.",
            "P0",
            "UAT",
        ),
        (
            "UAT-05",
            "Старт соревнования (истечение приглашений)",
            "Начать соревнование при наличии необработанных приглашений.",
            "Старт выполнен; все pending-приглашения истекли и недоступны "
            "для принятия/отклонения.",
            "P0",
            "UAT",
        ),
        (
            "UAT-06",
            "Оценки и завершение при частичных оценках",
            "Судьи выставляют оценки и комментарии; завершить соревнование при частичных оценках.",
            "Завершение разрешено; в средние включены только оценившие судьи.",
            "P0",
            "UAT",
        ),
        (
            "UAT-07",
            "Результаты, графики, PDF",
            "Судьи просматривают результаты, сортировки, фильтры, графики, выгружают CSV/PNG/PDF.",
            "Расчёты корректны (округление half-to-even); PDF соответствует шаблону.",
            "P0",
            "UAT",
        ),
        (
            "UAT-08",
            "Уведомления",
            "Проверить push/email уведомления по всем событиям и настройкам.",
            "Уведомления приходят согласно ТЗ; дублирование на email по опции.",
            "P0",
            "UAT",
        ),
        (
            "UAT-09",
            "Отключение сети",
            "Отключить сеть во время выставления оценок, затем восстановить.",
            "Данные не теряются; локальная очередь синхронизируется без дублей.",
            "P0",
            "UAT",
        ),
        (
            "UAT-10",
            "Конфликт двух устройств",
            "Одному судье выставить разные оценки офлайн на двух устройствах, "
            "затем синхронизировать.",
            "Применяется LWW; проигравшая запись в аудите; уведомление пользователю; дублей нет.",
            "P0",
            "UAT",
        ),
        (
            "UAT-11",
            "Удаление соревнования",
            "Удалить соревнование до старта и (другое) после завершения.",
            "Удаление разрешено только в этих состояниях; судьи уведомлены; "
            "в списках не отображается.",
            "P0",
            "UAT",
        ),
        (
            "UAT-12",
            "Метрики приёмки",
            "Собрать метрики UAT по опросу 10 судей.",
            "SUS ≥ 80; 90% без обучения; «выставить оценку» ≤ 3 действий; обучение ≤ 20 минут.",
            "P0",
            "UAT",
        ),
    ],
}

PRIORITY_COLORS = {"P0": "#d32f2f", "P1": "#f57c00", "P2": "#fbc02d", "P3": "#9e9e9e"}
TAG_COLORS = {
    "Functional": "#388e3c",
    "Negative": "#c2185b",
    "Boundary": "#7b1fa2",
    "Security": "#b71c1c",
    "Access": "#00796b",
    "Performance": "#1976d2",
    "Integration": "#0288d1",
    "Reliability": "#455a64",
    "Concurrency": "#5d4037",
    "Audit": "#616161",
    "UI": "#0097a7",
    "Localization": "#689f38",
    "Legal": "#8d6e63",
    "TTL/Job": "#512da8",
    "Property": "#303f9f",
    "Accessibility": "#009688",
    "Compatibility": "#00838f",
    "Retention": "#795548",
    **TAG_COLORS_EXTRA,
}


def create_extra_cases(api: TestyAPI, project_id: int) -> None:
    suites = {s["name"]: s["id"] for s in api.list_suites(project_id)}
    existing = {
        (c["suite"]["id"], c["name"]) for c in api.list_cases(project_id) if c.get("suite")
    }

    parent_ids: dict[str, int] = {}
    created = 0
    for (parent_title, suite_name), rows in EXTRA_SUITES.items():
        if parent_title not in parent_ids:
            if parent_title not in suites:
                suite = api.create_suite(
                    parent_title, project_id, None, "Импортировано из docs/план_тестирования.md"
                )
                suites[parent_title] = suite["id"]
                print(f"  + набор (корень): {parent_title}")
            parent_ids[parent_title] = suites[parent_title]
        if suite_name not in suites:
            suite = api.create_suite(suite_name, project_id, parent_ids[parent_title], "")
            suites[suite_name] = suite["id"]
            print(f"  + набор: {suite_name}")

        suite_id = suites[suite_name]
        for cid, name, scenario, expected, priority, tag in rows:
            case_name = f"{cid} {name}"
            if (suite_id, case_name) in existing:
                continue
            api.create_case(
                {
                    "name": case_name,
                    "project": project_id,
                    "suite": suite_id,
                    "setup": "",
                    "scenario": scenario,
                    "expected": expected,
                    "teardown": "",
                    "description": (
                        "Источник: docs/план_тестирования.md v1.0, "
                        f"{cid}. Приоритет: {priority}. Тип: {tag}."
                    ),
                    "is_steps": False,
                    "labels": [
                        {"name": priority, "color": PRIORITY_COLORS.get(priority, "")},
                        {"name": tag, "color": TAG_COLORS.get(tag, "")},
                    ],
                },
            )
            created += 1
    print(f"Часть 1: создано дополнительных кейсов: {created}")


# ---------------------------------------------------------------------------
# Часть 2: пользователи и план с распределением задач из xlsx
# ---------------------------------------------------------------------------


def read_assignments() -> list[dict]:
    """Читает docs/распределение_задач.xlsx и возвращает назначения."""
    rows: list[dict] = []
    text_lines: list[str] = []

    class _Collector:
        def write(self, s: str) -> None:
            text_lines.append(s)

        def flush(self) -> None:
            pass

    import contextlib

    with contextlib.redirect_stdout(_Collector()):
        dump_xlsx.main(str(XLSX))
    for line in "\n".join(text_lines).splitlines():
        parts = line.split(" | ")
        if len(parts) < 11 or not parts[1].strip()[:1].isalpha():
            continue
        title = parts[1].strip()
        if "-" not in title.split(" ")[0]:
            continue
        rows.append(
            {
                "case_prefix": title.split(" ")[0],
                "assignee": parts[4].strip(),
                "deadline": parts[7].strip(),
                "board": parts[9].strip(),
                "priority": parts[10].strip(),
            },
        )
    return rows


def ensure_users(api: TestyAPI) -> dict[str, int]:
    users = {u["username"]: u["id"] for u in api.get_all("/api/v2/users/")}
    name_to_id: dict[str, int] = {}
    for full_name, (username, first, last, email) in TEAM.items():
        if username not in users:
            api._post(
                "/api/v2/users/",
                {
                    "username": username,
                    "first_name": first,
                    "last_name": last,
                    "email": email,
                    "password": USER_PASSWORD,
                },
            )
            users = {u["username"]: u["id"] for u in api.get_all("/api/v2/users/")}
            print(f"  + пользователь: {username} ({full_name}), пароль: {USER_PASSWORD}")
        name_to_id[full_name] = users[username]
    return name_to_id


def create_assigned_plan(api: TestyAPI, project_id: int) -> None:
    plans = api.get_all(f"/api/v2/testplans/?project={project_id}")
    if any(p["name"] == PLAN_NAME for p in plans):
        print(f"Часть 2: план «{PLAN_NAME}» уже существует — пропускаем.")
        return

    assignments = read_assignments()
    print(f"  из xlsx прочитано назначений: {len(assignments)}")
    if not assignments:
        raise RuntimeError("Не удалось прочитать назначения из docs/распределение_задач.xlsx")

    name_to_id = ensure_users(api)

    # все функциональные кейсы (наборы 7.x)
    suites = {s["name"]: s["id"] for s in api.list_suites(project_id)}
    func_suite_ids = {sid for name, sid in suites.items() if name.startswith("7.")}
    cases = [c for c in api.list_cases(project_id) if c["suite"]["id"] in func_suite_ids]
    case_by_prefix = {c["name"].split(" ")[0]: c for c in cases}
    missing = [a["case_prefix"] for a in assignments if a["case_prefix"] not in case_by_prefix]
    if missing:
        print(
            f"  ВНИМАНИЕ: в TestY нет кейсов: {', '.join(sorted(set(missing)))}", file=sys.stderr
        )

    started = (
        min(a["deadline"] for a in assignments if a.get("deadline"))
        if assignments
        else "2026-12-01"
    )
    due = (
        max(a["deadline"] for a in assignments if a.get("deadline"))
        if assignments
        else "2026-12-10"
    )

    # нормализуем даты к ISO (дд.мм.гггг)
    def to_iso(d: str) -> str:
        dd, mm, yyyy = d.split(".")
        return f"{yyyy}-{mm}-{dd}"

    plan = api._post(
        "/api/v2/testplans/",
        {
            "name": PLAN_NAME,
            "project": project_id,
            "description": (
                "Все 196 функциональных кейсов раздела 7 плана тестирования. "
                "Исполнители и дедлайны — из docs/распределение_задач.xlsx (доски 01–12). "
                "Создан автоматически скриптом tools/testy_import/import_full_coverage.py."
            ),
            "test_cases": [
                case_by_prefix[a["case_prefix"]]["id"]
                for a in assignments
                if a["case_prefix"] in case_by_prefix
            ],
            "started_at": to_iso(started),
            "due_date": to_iso(due),
        },
    )
    plan_id = plan["id"] if isinstance(plan, dict) else plan[0]["id"]
    print(f"  + план «{PLAN_NAME}» (id={plan_id})")

    tests = api.get_all(f"/api/v2/tests/?project={project_id}&plan={plan_id}")
    test_by_case = {}
    for t in tests:
        cid = t["name"].split(" ")[0] if t.get("name") else None
        test_by_case[cid] = t["id"]
    assigned = 0
    for a in assignments:
        test_id = test_by_case.get(a["case_prefix"])
        user_id = name_to_id.get(a["assignee"])
        if test_id and user_id:
            api._request("PATCH", f"/api/v2/tests/{test_id}/", {"assignee": user_id})
            assigned += 1
    print(f"  назначено исполнителей на тесты: {assigned}")
    print(f"Часть 2: план с распределением задач готов. Даты: {to_iso(started)} … {to_iso(due)}.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Полное покрытие: доп. кейсы + пользователи + план с распределением"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="password")
    parser.add_argument("--project-id", type=int, default=1)
    args = parser.parse_args()

    api = TestyAPI(args.base_url, args.user, args.password)
    create_extra_cases(api, args.project_id)
    create_assigned_plan(api, args.project_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
