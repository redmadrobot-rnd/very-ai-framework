"""Админка — одна страница со встроенным скриптом, и сломать его легко: любая
незакрытая строка валит парсинг целиком, страница отдаёт 200 и не работает вообще.
Проверяем два таких тихих отказа: незакрытый литерал и onclick без обработчика.
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


def _unterminated_quote(line: str) -> str | None:
    """Кавычка, оставшаяся открытой к концу строки. Учитывает экранирование и то,
    что кавычка внутри строки в других кавычках — обычный символ."""
    quote = None
    escaped = False
    for ch in line:
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif quote is None and ch in "\"'":
            quote = ch
        elif ch == quote:
            quote = None
    return quote


def test_no_string_literal_spans_lines():
    for num, line in enumerate(_script().splitlines(), 1):
        assert _unterminated_quote(line) is None, (
            f"admin.html: строка {num} — литерал не закрыт: {line.strip()[:70]}"
        )


def test_handlers_referenced_from_html_exist():
    """onclick="foo()" без function foo — тоже тихая поломка кнопки."""
    called = set(re.findall(r'on\w+="(\w+)\(', ADMIN.read_text(encoding="utf-8")))
    defined = set(re.findall(r"function (\w+)", _script()))
    assert called <= defined, f"нет обработчиков: {sorted(called - defined)}"


def test_esc_covers_attribute_and_text_context():
    m = re.search(r"const ESCAPES = \{([^}]*)\}", _script())
    assert m, "не найден словарь экранирования"
    for ch in ("&", "<", ">", '"', "'"):
        assert f"{ch}'" in m.group(1) or f'{ch}"' in m.group(1), (
            f"esc() не экранирует {ch!r}"
        )


def test_no_server_value_lands_inside_an_inline_handler():
    """Браузер декодирует HTML-сущности ДО парсинга on*-атрибута, поэтому esc() там
    не спасает: апостроф в метке юзера рвал onclick и кнопка отзыва молча умирала.
    Значения обязаны ехать через data-*, а обработчик — быть делегированным."""
    bad = re.findall(r'on\w+="[^"]*\$\{[^"]*"', ADMIN.read_text(encoding="utf-8"))
    assert not bad, f"значение подставлено внутрь inline-обработчика: {bad[:2]}"
