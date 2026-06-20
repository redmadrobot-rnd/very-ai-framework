"""Построение поискового индекса (SQLite FTS5) и статистика.

Индекс — производное от md, полностью пересобирается командой `index`.
Таблицы: fts (bm25), tri (trigram, опционально), files (реестр), meta (флаги/версия).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from . import VERSION
from .core import DB_REL, area_of, chunk_md, iter_md, title_of


def _has_trigram(con) -> bool:
    """Поддерживает ли сборка SQLite trigram-токенайзер."""
    try:
        con.execute("CREATE VIRTUAL TABLE _tri_probe USING fts5(x, tokenize='trigram')")
        con.execute("DROP TABLE _tri_probe")
        return True
    except sqlite3.OperationalError:
        return False


def cmd_index(root: Path) -> dict:
    """Пересобрать индекс по всем *.md под root."""
    db = root / DB_REL
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    try:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING "
                    "fts5(path UNINDEXED, heading, lineno UNINDEXED, body, "
                    "tokenize='unicode61 remove_diacritics 2')")
    except sqlite3.OperationalError as e:
        print(f"ОШИБКА: SQLite без FTS5 ({e}). Нужен python с FTS5.", file=sys.stderr)
        sys.exit(2)
    has_tri = _has_trigram(con)
    if has_tri:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS tri USING "
                    "fts5(path UNINDEXED, heading, lineno UNINDEXED, body, tokenize='trigram')")
    con.execute("CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, title TEXT, "
                "area TEXT, size INT, chunks INT)")
    con.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
    for t in ("fts", "files"):
        con.execute(f"DELETE FROM {t}")
    if has_tri:
        con.execute("DELETE FROM tri")

    files = {}
    for p in iter_md(root):
        rel = p.relative_to(root).as_posix()
        try:
            files[rel] = p.read_text(encoding="utf-8")
        except Exception:
            continue

    nchunks = 0
    for rel, text in files.items():
        title, area = title_of(text, rel), area_of(rel)
        chs = chunk_md(text)
        for line, heading, body in chs:
            con.execute("INSERT INTO fts(path,heading,lineno,body) VALUES(?,?,?,?)",
                        (rel, heading, str(line), body))
            if has_tri:
                con.execute("INSERT INTO tri(path,heading,lineno,body) VALUES(?,?,?,?)",
                            (rel, heading, str(line), body))
        nchunks += len(chs)
        con.execute("INSERT INTO files VALUES(?,?,?,?,?)",
                    (rel, title, area, len(text.encode("utf-8")), len(chs)))
    con.execute("INSERT OR REPLACE INTO meta VALUES('trigram', ?)", ("1" if has_tri else "0",))
    con.execute("INSERT OR REPLACE INTO meta VALUES('version', ?)", (VERSION,))
    con.commit()
    con.close()
    return {"files": len(files), "chunks": nchunks,
            "trigram": has_tri, "db": str(db.relative_to(root))}


def cmd_stat(root: Path) -> dict:
    """Статистика индекса (без перестроения)."""
    db = root / DB_REL
    if not db.exists():
        return {"indexed": False}
    con = sqlite3.connect(db)
    f = con.execute("SELECT count(*), coalesce(sum(size),0), coalesce(sum(chunks),0) FROM files").fetchone()
    areas = con.execute("SELECT count(DISTINCT area) FROM files").fetchone()[0]
    has_tri = (con.execute("SELECT v FROM meta WHERE k='trigram'").fetchone() or ("0",))[0] == "1"
    con.close()
    return {"indexed": True, "files": f[0], "bytes": f[1], "chunks": f[2],
            "areas": areas, "trigram": has_tri}
