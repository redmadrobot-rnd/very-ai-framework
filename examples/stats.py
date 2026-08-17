"""Маленький демо-модуль для пробы Codex-ревью и диалога в PR.

Намеренно простой и не покрыт тестами — чтобы было что обсудить в ревью.
"""

from __future__ import annotations


def average(numbers: list[float]) -> float:
    """Среднее арифметическое списка чисел."""
    return sum(numbers) / len(numbers)


def percent(part: float, whole: float) -> float:
    """Какой процент `part` составляет от `whole`."""
    return part / whole * 100.0


def median(numbers: list[float]) -> float:
    """Медиана списка чисел."""
    ordered = sorted(numbers)
    mid = len(ordered) // 2
    return ordered[mid]
