#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Импорт тест-кейсов из docs/план_тестирования.md в TestY TMS через REST API v2.

Скрипт разбирает таблицы тест-кейсов плана тестирования (разделы 7.x и 9.x)
и создаёт в TestY:
  * родительские наборы «Раздел 7 — Тест-кейсы по модулям» и «Раздел 9 — NFR-тесты»;
  * дочерние наборы по каждому подразделу (7.1…7.12, 9.1, 9.2, 9.4, 9.5, 9.7);
  * тест-кейсы (сценарного типа) с предусловиями, шагами, ожидаемым результатом;
  * метки приоритета (P0…P3) и типа (Functional, Security, …) с цветами.

Скрипт идемпотентен: существующие наборы и кейсы (по паре набор+имя) не дублируются.

Использование:
    python tools/testy_import/import_test_cases.py
    python tools/testy_import/import_test_cases.py --base-url http://127.0.0.1:8001 \
        --user admin --password password --project-id 1
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[2]
PLAN_PATH = WORKSPACE / "docs" / "план_тестирования.md"

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
}


class TestyAPI:
    """Минимальный клиент REST API TestY v2 (JWT + автопродление токена)."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.access: str | None = None
        self.refresh: str | None = None
        self._login()

    # -- auth -------------------------------------------------------------
    def _login(self) -> None:
        data = self._post(
            "/api/token/", {"username": self.username, "password": self.password}, auth=False
        )
        self.access, self.refresh = data["access"], data["refresh"]

    def _refresh(self) -> None:
        data = self._post("/api/token/refresh/", {"refresh": self.refresh}, auth=False)
        self.access = data["access"]

    # -- http -------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        auth: bool = True,
        retry: bool = True,
    ):
        url = f"{self.base_url}{path}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self.access}"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as err:
            if err.code == 401 and auth and retry:
                self._refresh()
                return self._request(method, path, payload, auth=auth, retry=False)
            detail = err.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {path} -> HTTP {err.code}: {detail}") from err

    def _post(self, path: str, payload: dict, auth: bool = True):
        return self._request("POST", path, payload, auth=auth)

    def get_all(self, path: str) -> list[dict]:
        """GET со сбором всех страниц пагинации."""
        items: list[dict] = []
        next_url: str | None = f"{self.base_url}{path}"
        while next_url:
            with urllib.request.urlopen(
                urllib.request.Request(
                    next_url,
                    headers={
                        "Authorization": f"Bearer {self.access}",
                        "Accept": "application/json",
                    },
                ),
                timeout=60,
            ) as response:
                data = json.loads(response.read().decode("utf-8"))
            items.extend(data.get("results", []))
            next_link = (data.get("links") or {}).get("next")
            next_url = next_link if next_link else None
        return items

    # -- domain -----------------------------------------------------------
    def list_suites(self, project_id: int) -> list[dict]:
        return self.get_all(f"/api/v2/suites/?project={project_id}")

    def create_suite(
        self, name: str, project_id: int, parent: int | None, description: str = ""
    ) -> dict:
        return self._post(
            "/api/v2/suites/",
            {"name": name, "project": project_id, "parent": parent, "description": description},
        )

    def list_cases(self, project_id: int) -> list[dict]:
        return self.get_all(f"/api/v2/cases/?project={project_id}")

    def create_case(self, payload: dict) -> dict:
        return self._post("/api/v2/cases/", payload)


# ---------------------------------------------------------------------------
# Разбор плана тестирования
# ---------------------------------------------------------------------------


def unescape(cell: str) -> str:
    """Убирает markdown-экранирование: \\| \\. \\< \\> \\+ \\- \\= и т.п."""
    return re.sub(r"\\(.)", r"\1", cell).strip()


def split_row(line: str) -> list[str]:
    return [unescape(c) for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]


def format_steps(text: str) -> str:
    """Переносит нумерованные шаги на отдельные строки."""
    parts = re.split(r"\s+(?=\d+\.\s)", text.strip())
    return "\n".join(p.strip() for p in parts if p.strip())


def parse_plan(text: str) -> list[dict]:
    """Возвращает список подразделов: {section, title, parent_title, rows: [case_dict]}."""
    sections: list[dict] = []
    current: dict | None = None
    header: list[str] | None = None
    skip_separator = False

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        section_match = re.match(r"^##\s+(7|9)\\?\.\s*(.*)$", line)
        subsection_match = re.match(r"^###\s+((?:7|9)\.\d+)\s*(.*)$", line)
        other_heading = re.match(r"^##\s", line)

        if subsection_match:
            number, title = subsection_match.group(1), subsection_match.group(2).strip()
            parent_title = (
                "Раздел 7 — Тест-кейсы по модулям"
                if number.startswith("7")
                else "Раздел 9 — NFR-тесты"
            )
            current = {"section": number, "title": title, "parent_title": parent_title, "rows": []}
            sections.append(current)
            header = None
            continue
        if other_heading and not section_match:
            current = None
            header = None
            continue
        if section_match:
            continue
        if current is None:
            continue

        if line.startswith("|"):
            cells = split_row(line)
            if header is None:
                header = cells
                skip_separator = True
                continue
            if skip_separator:
                skip_separator = False
                continue
            row = map_row(header, cells, current["section"])
            if row:
                current["rows"].append(row)
        else:
            header = None
    return [s for s in sections if s["rows"]]


def map_row(header: list[str], cells: list[str], section: str) -> dict | None:
    """Сопоставляет строку таблицы плана тестирования с полями тест-кейса TestY."""
    data = dict(zip(header, cells))
    cid = data.get("ID", "").strip()
    if not cid:
        return None

    def val(key: str) -> str:
        value = data.get(key, "").strip()
        return "" if value == "—" else value

    priority = val("Приоритет")
    labels = []
    if priority:
        labels.append({"name": priority, "color": PRIORITY_COLORS.get(priority, "")})

    if "Шаги" in data:  # функциональные кейсы раздела 7 (кроме 7.12)
        name = val("Название")
        row = {
            "name": f"{cid} {name}",
            "setup": val("Предусловия"),
            "scenario": format_steps(val("Шаги")),
            "expected": val("Ожидаемый результат"),
            "tag": val("Тип"),
        }
    elif (
        "Шаги" not in data and "Ожидаемый результат" in data and "Название" in data
    ):  # 7.12 Безопасность
        name = val("Название")
        row = {
            "name": f"{cid} {name}",
            "setup": "",
            "scenario": f"Выполнить проверку «{name}» по методике раздела 9.3 плана тестирования.",
            "expected": val("Ожидаемый результат"),
            "tag": "Security",
        }
    elif "Критерий" in data:  # 9.1 Производительность
        row = {
            "name": f"{cid} {val('Требование')}",
            "setup": "",
            "scenario": f"Метод: {val('Метод')}.",
            "expected": val("Критерий"),
            "tag": "Performance",
        }
    elif "Устройство" in data:  # 9.4 Совместимость
        row = {
            "name": f"{cid} Совместимость: {val('Устройство')}",
            "setup": "",
            "scenario": f"Проверки: {val('Проверки')}.",
            "expected": "Установка, запуск и ключевые сценарии работают без замечаний.",
            "tag": "Compatibility",
        }
    elif "Метод" in data:  # 9.2 Надёжность, 9.5 Доступность, 9.7 Хранение
        tags = {"9.2": "Reliability", "9.5": "Accessibility", "9.7": "Retention"}
        row = {
            "name": f"{cid} {val('Требование')}",
            "setup": "",
            "scenario": f"Метод: {val('Метод')}.",
            "expected": "Результат соответствует требованию плана тестирования.",
            "tag": tags.get(section, "Functional"),
        }
    else:
        return None

    if row["tag"]:
        labels.append({"name": row["tag"], "color": TAG_COLORS.get(row["tag"], "")})
    row["labels"] = labels
    row["priority"] = priority
    row["cid"] = cid
    return row


# ---------------------------------------------------------------------------
# Импорт
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Импорт тест-кейсов плана тестирования в TestY")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="password")
    parser.add_argument("--project-id", type=int, default=1)
    args = parser.parse_args()

    if not PLAN_PATH.exists():
        print(f"Файл плана не найден: {PLAN_PATH}", file=sys.stderr)
        return 1

    text = PLAN_PATH.read_text(encoding="utf-8")
    sections = parse_plan(text)
    total_rows = sum(len(s["rows"]) for s in sections)
    print(f"Разобрано подразделов: {len(sections)}, тест-кейсов: {total_rows}")

    api = TestyAPI(args.base_url, args.user, args.password)

    # Существующие наборы и кейсы (идемпотентность)
    suites = {s["name"]: s["id"] for s in api.list_suites(args.project_id)}
    existing_cases = {
        (c["suite"]["id"], c["name"]) for c in api.list_cases(args.project_id) if c.get("suite")
    }
    print(f"В проекте уже есть наборов: {len(suites)}, кейсов: {len(existing_cases)}")

    parent_ids: dict[str, int] = {}
    created_cases = skipped_cases = 0

    for section in sections:
        parent_title = section["parent_title"]
        if parent_title not in parent_ids:
            if parent_title in suites:
                parent_ids[parent_title] = suites[parent_title]
            else:
                parent = api.create_suite(
                    parent_title,
                    args.project_id,
                    None,
                    "Импортировано из docs/план_тестирования.md",
                )
                parent_ids[parent_title] = parent["id"]
                suites[parent_title] = parent["id"]
                print(f"  + набор (корень): {parent_title}")

        suite_name = f"{section['section']} {section['title']}"
        if suite_name in suites:
            suite_id = suites[suite_name]
        else:
            suite = api.create_suite(suite_name, args.project_id, parent_ids[parent_title], "")
            suite_id = suite["id"]
            suites[suite_name] = suite_id
            print(f"  + набор: {suite_name}")

        for row in section["rows"]:
            if (suite_id, row["name"]) in existing_cases:
                skipped_cases += 1
                continue
            description = (
                f"Источник: docs/план_тестирования.md v1.0, раздел "
                f"{section['section']}, {row['cid']}. "
                f"Приоритет: {row['priority'] or '—'}. Тип: {row['tag'] or '—'}."
            )
            api.create_case(
                {
                    "name": row["name"],
                    "project": args.project_id,
                    "suite": suite_id,
                    "setup": row["setup"],
                    "scenario": row["scenario"],
                    "expected": row["expected"],
                    "teardown": "",
                    "description": description,
                    "is_steps": False,
                    "labels": row["labels"],
                },
            )
            created_cases += 1
        print(f"    набор «{suite_name}»: кейсов в таблице {len(section['rows'])}")

    print(f"\nГотово: создано кейсов {created_cases}, пропущено (уже были) {skipped_cases}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
