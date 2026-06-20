#!/usr/bin/env python3
"""GitMark — CLI базы знаний на чистом md + README + git. Точка входа.

Команды:
    gitmark index  [--root .]                построить/обновить индекс
    gitmark search "<q>" [-k 8] [--json]     искать (bm25 ∪ trigram ∪ fuzzy)
    gitmark stat                             статистика индекса/БЗ
    gitmark lint  [paths…] [--strict] [--json]  проверить онтологию (I1–I6)
    gitmark version
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

# Windows-консоль (cp1251) роняет вывод с ✓/кириллицей — форсируем UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Гарантируем импорт пакета gm/ рядом с этим файлом (при запуске по абсолютному пути).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gm import VERSION
from gm.core import repo_root
from gm.index import cmd_index, cmd_stat
from gm.lint import INVARIANTS, cmd_lint
from gm.search import cmd_search


def _print_search(res: list, as_json: bool):
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    if not res:
        print("ничего не найдено")
        return
    for r in res:
        head = f" › {r['heading']}" if r["heading"] else ""
        print(f"{r['path']}:{r['line']}{head}  [{r['via']}]")
        print(f"   {r['snippet'][:200]}")


def _print_lint(r: dict, as_json: bool = False):
    issues = r["issues"]
    if as_json:
        print(json.dumps({
            "checked": r["checked"],
            "issues": [{"level": l, "code": c, "path": p, "line": ln, "msg": m}
                       for l, c, p, ln, m in issues],
        }, ensure_ascii=False, indent=2))
        return
    order = {"ERR": 0, "WARN": 1}
    for lvl, code, path, line, msg in sorted(issues, key=lambda i: (order[i[0]], i[1], i[2], i[3])):
        loc = f"{path}:{line}" if line else path
        print(f"{lvl:<4} {code}  {loc} — {msg}")
    by_code = Counter(code for _, code, _, _, _ in issues)
    by_lvl = Counter(lvl for lvl, *_ in issues)
    summary = " · ".join(f"{c}×{n}" for c, n in sorted(by_code.items())) or "—"
    head = "✓ чисто" if not issues else f"{by_lvl['ERR']} ERR · {by_lvl['WARN']} WARN"
    print(f"\n{head}  ({r['checked']} файлов · {summary})")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gitmark", description="GitMark — md+git knowledge base CLI")
    ap.add_argument("--root", default=None, help="корень репо (по умолчанию — авто по .git)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index", help="построить индекс")
    sp = sub.add_parser("search", help="искать")
    sp.add_argument("query")
    sp.add_argument("-k", type=int, default=8)
    sp.add_argument("--json", action="store_true")
    sub.add_parser("stat", help="статистика")
    lp = sub.add_parser(
        "lint", help="проверить онтологию",
        epilog="инварианты:\n" + "\n".join(f"  {c}  {d}" for c, d in INVARIANTS.items()),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    lp.add_argument("paths", nargs="*", help="ограничить файлами (по умолчанию — все docs/)")
    lp.add_argument("--strict", action="store_true", help="exit 1 при любых ERR")
    lp.add_argument("--json", action="store_true", help="вывести находки в JSON")
    sub.add_parser("version", help="версия")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve() if a.root else repo_root(Path.cwd())

    if a.cmd == "index":
        r = cmd_index(root)
        print(f"✓ index: {r['files']} файлов · {r['chunks']} чанков · "
              f"trigram={'on' if r['trigram'] else 'OFF'} → {r['db']}")
    elif a.cmd == "search":
        _print_search(cmd_search(root, a.query, a.k), a.json)
    elif a.cmd == "stat":
        s = cmd_stat(root)
        if not s.get("indexed"):
            print("индекс не построен — `gitmark index`")
            return
        print(f"GitMark · {s['files']} файлов · {s['areas']} папок · {s['chunks']} чанков · "
              f"{s['bytes']//1024} KB · trigram={'on' if s['trigram'] else 'off'}")
    elif a.cmd == "lint":
        r = cmd_lint(root, a.paths or None)
        _print_lint(r, a.json)
        if a.strict and any(i[0] == "ERR" for i in r["issues"]):
            sys.exit(1)
    elif a.cmd == "version":
        print(f"gitmark {VERSION}")


if __name__ == "__main__":
    main()
