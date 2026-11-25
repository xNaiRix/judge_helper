"""Утилиты: нормализация ФИО и округление результатов."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_EVEN, Decimal


def normalize_fio(*parts: str | None) -> str:
    """Нормализация ФИО для проверки дубликатов (ТЗ, раздел «Добавление участников»).

    Правила:
      * обрезка пробелов по краям;
      * схлопывание внутренних повторных пробелов до одного;
      * приведение к нижнему регистру;
      * «ё» и «е» считаются эквивалентными;
      * дефисы и апострофы сохраняются как значимые символы.
    """
    full = " ".join(p.strip() for p in parts if p and p.strip())
    full = re.sub(r"\s+", " ", full)
    return full.lower().replace("ё", "е")


def round2(value: float | int | None) -> float | None:
    """Округление до 2 знаков по правилу banker's rounding (half-to-even).

    Пустой набор оценок (None) остаётся None, а не превращается в 0.
    """
    if value is None:
        return None
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def mean(values: list[float]) -> float | None:
    """Среднее арифметическое; для пустого списка возвращает None (не 0)."""
    if not values:
        return None
    return sum(values) / len(values)
