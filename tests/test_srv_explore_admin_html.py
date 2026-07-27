"""Админка — одна страница со встроенным скриптом, и сломать его легко: любая
незакрытая строка валит парсинг целиком, страница отдаёт 200 и не работает вообще.
Ловим самый частый случай — строковый литерал, раскатанный на несколько строк.
"""

from __future__ import annotations

import re
from pathlib import Path

ADMIN = Path(__file__).resolve().parents[1] / "srv_explore" / "admin.html"


def _script() -> str:
    m = re.search(r"<script>(.*)</script>", ADMIN.read_text(encoding="utf-8"), re.S)
    assert m, "в admin.html нет блока <script>"
    body = re.sub(r"`(?:\\.|[^`\\])*`", "``", m.group(1), flags=re.S)  # шаблоны — можно
    return re.sub(r"//[^\n]*", "", body)


def test_no_string_literal_spans_lines():
    for num, line in enumerate(_script().splitlines(), 1):
        clean = re.sub(r"\\.", "", line)
        for quote in ('"', "'"):
            assert clean.count(quote) % 2 == 0, (
                f"admin.html: строка {num} — нечётное число {quote}, "
                f"литерал не закрыт: {line.strip()[:70]}"
            )


def test_handlers_referenced_from_html_exist():
    """onclick="foo()" без function foo — тоже тихая поломка кнопки."""
    html = ADMIN.read_text(encoding="utf-8")
    body = _script()
    called = set(re.findall(r'on\w+="(\w+)\(', html))
    defined = set(re.findall(r"function (\w+)", body)) | set(
        re.findall(r"(?:async )?function (\w+)", body)
    )
    assert called <= defined, f"нет обработчиков: {sorted(called - defined)}"
