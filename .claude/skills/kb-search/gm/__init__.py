"""GitMark — база знаний на чистом md + README + git (поиск + онтология-линтер).

Пакет разбит на сфокусированные модули:
    core    — общие хелперы: обход репо, чанкование, резолв ссылок, константы
    index   — построение FTS5-индекса (+ stat)
    search  — поиск (bm25 ∪ trigram ∪ fuzzy)
    lint    — проверка инвариантов онтологии I1–I6

CLI-обёртка — в gitmark.py (точка входа).
"""

VERSION = "0.1.0"


def force_utf8_io() -> None:
    """Force UTF-8 on stdout/stderr.

    On Windows the console defaults to cp1251/cp866, so printing ✓/Cyrillic crashes
    with UnicodeEncodeError. Switch the streams to UTF-8 (errors='replace' so a legacy
    console degrades instead of crashing). Every entry point calls this first.
    """
    import sys

    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
