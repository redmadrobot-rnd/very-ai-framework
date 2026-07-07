"""Поиск по индексу: bm25 ∪ trigram (точная подстрока) ∪ fuzzy (4-символьные окна).

Результаты сливаются по (path, line); вес: bm25 ×1.0, trigram ×0.6, fuzzy ×0.3.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .core import DB_REL, HISTORICAL_TYPES

WORD_RE = re.compile(r"[\w\-]+", re.UNICODE)


def _fts_match_query(query: str) -> str:
    """Запрос для FTS5: префиксный OR по терминам ≥2 символов."""
    terms = [t for t in WORD_RE.findall(query) if len(t) >= 2]
    return " OR ".join(f'"{t}"*' for t in terms)


def _fuzzy_phrases(s: str) -> list:
    """4-символьные окна слов запроса → опечатки/морфология/кириллица.

    Матч по 4-символьным окнам (а не одиночным 3-граммам) отсекает мусор: опечатка
    сохраняет длинные общие подстроки (`firecraker`↔`firecracker`: `fire`,`ecra`),
    а случайная строка делит с доками лишь разрозненные частые 3-граммы (`ent`,`non`).
    """
    wins, seen = [], set()
    for w in WORD_RE.findall(s.lower()):
        if len(w) < 4:
            continue
        for i in range(len(w) - 3):
            win = w[i:i + 4]
            if '"' in win or win in seen:
                continue
            seen.add(win)
            wins.append(win)
    return wins


def cmd_search(root: Path, query: str, k: int = 8, scope: str = "live") -> list:
    """Искать по индексу. scope: live — без историч. типов (plan/report), history —
    только они, all — всё. Классификация — по колонке files.node_type."""
    db = root / DB_REL
    if not db.exists():
        raise SystemExit("Индекс не найден — запусти `gitmark index`")
    con = sqlite3.connect(db)
    has_tri = (con.execute("SELECT v FROM meta WHERE k='trigram'").fetchone() or ("0",))[0] == "1"

    # Пути историч. доков — фильтр по scope. Пустой список типов не ломает SQL.
    hist: set = set()
    if scope != "all" and HISTORICAL_TYPES:
        ph = ",".join("?" * len(HISTORICAL_TYPES))
        try:
            hist = {p for (p,) in con.execute(
                f"SELECT path FROM files WHERE node_type IN ({ph})",
                tuple(HISTORICAL_TYPES))}
        except sqlite3.OperationalError:
            hist = set()
    if scope == "history":
        keep = lambda p: p in hist  # noqa: E731
    elif scope == "all":
        keep = lambda p: True  # noqa: E731
    else:
        keep = lambda p: p not in hist  # noqa: E731
    results: dict = {}

    # bm25 — ранжировка по терминам (вес 1.0)
    bm_q = _fts_match_query(query)
    if bm_q:
        try:
            for path, heading, lineno, snip, score in con.execute(
                "SELECT path,heading,lineno,"
                "snippet(fts,3,'»','«','…',14), bm25(fts) "
                "FROM fts WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT ?",
                (bm_q, k * 3),
            ):
                results[(path, lineno)] = {
                    "path": path, "heading": heading, "line": int(lineno),
                    "snippet": " ".join(snip.split()), "score": -float(score), "via": "bm25"}
        except sqlite3.OperationalError:
            pass

    if has_tri and len(query.strip()) >= 3:
        # (a) фразовый trigram — точная подстрока (вес 0.6)
        try:
            tq = '"' + query.replace('"', " ").strip() + '"'
            for path, heading, lineno, snip, score in con.execute(
                "SELECT path,heading,lineno,"
                "snippet(tri,3,'»','«','…',14), bm25(tri) "
                "FROM tri WHERE tri MATCH ? ORDER BY bm25(tri) LIMIT ?",
                (tq, k * 2),
            ):
                key = (path, lineno)
                if key not in results:
                    results[key] = {
                        "path": path, "heading": heading, "line": int(lineno),
                        "snippet": " ".join(snip.split()), "score": -float(score) * 0.6,
                        "via": "trigram"}
        except sqlite3.OperationalError:
            pass
        # (b) fuzzy: OR по 4-символьным окнам. Чанк принимается, только если содержит
        # ≥ceil(20%) (и ≥1) различных окон запроса — отсекает мусор (вес 0.3).
        grams = _fuzzy_phrases(query)
        if grams:
            fq = " OR ".join(f'"{g}"' for g in grams)
            need = max(1, (len(grams) + 4) // 5)
            try:
                for path, heading, lineno, snip, body, score in con.execute(
                    "SELECT path,heading,lineno,"
                    "snippet(tri,3,'»','«','…',14), body, bm25(tri) "
                    "FROM tri WHERE tri MATCH ? ORDER BY bm25(tri) LIMIT ?",
                    (fq, k * 3),
                ):
                    key = (path, lineno)
                    if key in results:
                        continue
                    if sum(1 for g in grams if g in body.lower()) < need:
                        continue
                    results[key] = {
                        "path": path, "heading": heading, "line": int(lineno),
                        "snippet": " ".join(snip.split()), "score": -float(score) * 0.3,
                        "via": "fuzzy"}
            except sqlite3.OperationalError:
                pass
    con.close()
    hits = [r for r in results.values() if keep(r["path"])]
    return sorted(hits, key=lambda r: -r["score"])[:k]
