#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Создание демонстрационного тест-плана «Smoke-регрессия» в TestY.

План собирается из ключевых P0-кейсов согласно разделу 11 плана тестирования:
вход, список, создание, приглашение, участник, старт (истечение приглашений),
оценка, завершение, результаты, PDF, удаление.

Использование:
    python tools/testy_import/create_smoke_plan.py
        [--base-url ...] [--user ...] [--password ...] [--project-id 1]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_test_cases import TestyAPI  # noqa: E402

PLAN_NAME = "Smoke-регрессия (демо)"
PLAN_DESCRIPTION = (
    "Демонстрационный smoke-план по разделу 11 плана тестирования: "
    "вход, список, создание, приглашение, участник, старт (истечение приглашений), "
    "оценка, завершение, результаты, PDF, удаление."
)

# Префиксы ID кейсов, входящих в smoke-набор (порядок = порядок прогонов)
SMOKE_CASE_PREFIXES = [
    "AUTH-01",
    "AUTH-02",
    "AUTH-04",
    "LIST-01",
    "LIST-02",
    "COMP-01",
    "COMP-05",
    "COMP-09",
    "COMP-19",
    "PART-01",
    "SCORE-01",
    "SCORE-03",
    "FIN-03",
    "CHART-01",
    "PDF-01",
    "DEL-01",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Создание smoke-плана в TestY")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="password")
    parser.add_argument("--project-id", type=int, default=1)
    args = parser.parse_args()

    api = TestyAPI(args.base_url, args.user, args.password)

    plans = api.get_all(f"/api/v2/testplans/?project={args.project_id}")
    if any(p["name"] == PLAN_NAME for p in plans):
        print(f"План «{PLAN_NAME}» уже существует — пропускаем.")
        return 0

    cases = api.list_cases(args.project_id)
    selected: list[int] = []
    missing: list[str] = []
    for prefix in SMOKE_CASE_PREFIXES:
        found = next((c for c in cases if c["name"].startswith(prefix)), None)
        if found:
            selected.append(found["id"])
        else:
            missing.append(prefix)
    if missing:
        print(f"ВНИМАНИЕ: не найдены кейсы: {', '.join(missing)}", file=sys.stderr)

    today = date.today()
    plan = api._post(
        "/api/v2/testplans/",
        {
            "name": PLAN_NAME,
            "project": args.project_id,
            "description": PLAN_DESCRIPTION,
            "test_cases": selected,
            "started_at": today.isoformat(),
            "due_date": (today + timedelta(days=30)).isoformat(),
        },
    )
    plan_id = plan["id"] if isinstance(plan, dict) else (plan[0].get("id") if plan else "n/a")
    print(f"Создан тест-план «{PLAN_NAME}» (id={plan_id}), тестов в плане: {len(selected)}.")
    print(
        "Дальше: раздел Test Plans & Results → открыть план → создать "
        "Test Run и фиксировать результаты."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
