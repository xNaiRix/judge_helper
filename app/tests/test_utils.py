"""Unit-тесты утилит: нормализация ФИО и округление (banker's rounding)."""

from __future__ import annotations

from decimal import Decimal

from judge_helper.utils import mean, normalize_fio, round2


class TestNormalizeFio:
    def test_simple(self):
        assert normalize_fio("Иванов", "Иван", "Иванович") == "иванов иван иванович"

    def test_trim_and_collapse_spaces(self):
        assert normalize_fio("  Иванов ", "Иван", "Иванович") == "иванов иван иванович"
        assert normalize_fio("Иванов", "  Иван   Иванович ") == "иванов иван иванович"
        assert normalize_fio("Иванов\tИван", "Иванович") == "иванов иван иванович"

    def test_case_insensitive(self):
        assert normalize_fio("иВаНоВ", "иВАН") == normalize_fio("Иванов", "Иван")

    def test_yo_equals_e(self):
        assert normalize_fio("Артём", "Ёлкин") == normalize_fio("Артем", "Елкин")

    def test_hyphen_is_significant(self):
        # Дефисы и апострофы сохраняются как значимые символы (ТЗ)
        assert normalize_fio("Римский-Корсаков", "Николай") != normalize_fio(
            "РимскийКорсаков", "Николай"
        )

    def test_apostrophe_is_significant(self):
        assert normalize_fio("Д'Артаньян", "Огюст") != normalize_fio("Дартаньян", "Огюст")

    def test_empty_parts_ignored(self):
        assert normalize_fio("Иванов", "Иван", "") == "иванов иван"
        assert normalize_fio("Иванов", None, "Иванович") == "иванов иванович"

    def test_only_spaces_gives_empty(self):
        assert normalize_fio("   ", "  ") == ""


class TestRound2:
    def test_none_stays_none(self):
        # Пустой набор оценок даёт null, а не 0 (ТЗ)
        assert round2(None) is None

    def test_simple(self):
        assert round2(10) == 10.0
        assert round2(9.5) == 9.5

    def test_half_to_even(self):
        # banker's rounding: половина округляется к чётной цифре
        assert round2(2.345) == 2.34  # 4 — чётная
        assert round2(2.355) == 2.36  # 6 — чётная
        assert round2(0.125) == 0.12
        assert round2(0.135) == 0.14

    def test_two_decimal_places(self):
        assert round2(9.333333) == 9.33
        assert round2(9.666666) == 9.67
        assert round2(14 / 3) == 4.67

    def test_returns_float(self):
        assert isinstance(round2(1), float)


class TestMean:
    def test_empty_is_none(self):
        # Пустой набор — null, деления на ноль нет (ТЗ)
        assert mean([]) is None
        assert mean(None) is None

    def test_simple(self):
        assert mean([10, 9]) == 9.5
        assert mean([5]) == 5

    def test_decimal_input(self):
        assert mean([Decimal("1"), Decimal("2")]) == 1.5
