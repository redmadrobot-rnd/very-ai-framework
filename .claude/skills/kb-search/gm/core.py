"""Общие хелперы: обход репозитория, метаданные документов, резолв md-ссылок.

Используется всеми остальными модулями (index/search/lint). Чистый stdlib.
"""
from __future__ import annotations

import posixpath
import re
import unicodedata
from pathlib import Path

# Папки, которые не индексируем и не линтуем.
EXCLUDE_DIRS = {
    ".git", "node_modules", ".next", "dist", "build", "__pycache__",
    ".pytest_cache", "_vendor", ".venv", "venv", "vendor",
    ".gitmark", ".worktrees", "worktrees",
}
# Корень онтологической БЗ. Сканируем только его — `docs/` может содержать и не-KB
# вещи (картинки, генерёжка, прочая документация). Сменить путь — здесь, одной строкой.
KB_DIR = "docs/gitmark"
# Производный индекс — в .gitmark/, она в .gitignore.
DB_REL = ".gitmark/index.db"

# «Исторические» node_type — датированные снапшоты (замысел до реализации, разовый
# аудит). Остаются в онтологии (типизированы, связаны, линтуются, на графе), но
# `search` по умолчанию (scope=live) их прячет: после реализации план расходится с
# фактом, релевантность аудита падает. Полное знание достаётся `search --scope all`.
HISTORICAL_TYPES = {"plan", "report"}

HEAD_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
# значение может быть в кавычках (`node_type: "plan"`) — линт их принимает, индекс тоже.
NODE_TYPE_RE = re.compile(r"""^\s*node_type\s*:\s*['"]?([\w-]+)""", re.MULTILINE)
H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
# markdown-ссылка [text](href) — не картинка (отрицательный lookbehind на `!`)
LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)]+)\)")


def repo_root(start: Path) -> Path:
    """Корень репозитория — ближайший родитель с .git (иначе сам start)."""
    p = start.resolve()
    for cand in [p, *p.parents]:
        if (cand / ".git").exists():
            return cand
    return p


def iter_md(root: Path):
    """Все *.md базы знаний — это `KB_DIR` под root, отсортированные по пути.

    БЗ = `KB_DIR` (docs/gitmark). Остальной репозиторий (код, корневые README/CLAUDE,
    прочая документация в docs/) НЕ часть БЗ и не сканируется — поэтому никаких списков
    исключений не нужно: scope задаётся одной папкой. Пути отдаём относительно
    root (`docs/gitmark/...`), как ожидают index/search/lint/graph.
    """
    kb = root / KB_DIR
    if not kb.is_dir():
        return
    for p in sorted(kb.rglob("*.md")):
        if any(part in EXCLUDE_DIRS for part in p.relative_to(root).parts):
            continue
        yield p


def node_type_of(text: str) -> str | None:
    """`node_type` из frontmatter документа (или None, если нет FM/ключа)."""
    m = FM_RE.match(text)
    if not m:
        return None
    t = NODE_TYPE_RE.search(m.group(1))
    return t.group(1).strip() if t else None


def kb_subpath(rel: str) -> str:
    """Путь относительно KB_DIR (для классификации внутри БЗ)."""
    return rel[len(KB_DIR) + 1:] if rel.startswith(KB_DIR + "/") else rel


def area_of(rel: str) -> str:
    """Группа документа внутри БЗ (для статистики/кластеров графа).

    services/<svc> — отдельная группа; прочее — по первому подкаталогу; верхний
    уровень БЗ — «(root)». Считается относительно KB_DIR.
    """
    parts = kb_subpath(rel).split("/")
    if len(parts) == 1:
        return "(root)"
    if parts[0] == "services" and len(parts) > 2:
        return "services/" + parts[1]
    return parts[0]


def title_of(text: str, rel: str) -> str:
    """Заголовок документа — первый H1, иначе имя файла."""
    m = H1_RE.search(text)
    if m:
        return re.sub(r"[`*]", "", m.group(1)).strip()[:90]
    return Path(rel).name


def chunk_md(text: str):
    """Чанкуем по заголовкам → список (line_start, heading, body)."""
    lines = text.split("\n")
    chunks, cur = [], {"line": 1, "heading": "", "body": []}
    for i, ln in enumerate(lines, 1):
        m = HEAD_RE.match(ln)
        if m:
            if cur["body"] or cur["heading"]:
                chunks.append((cur["line"], cur["heading"], "\n".join(cur["body"])))
            cur = {"line": i, "heading": m.group(2).strip(), "body": [ln]}
        else:
            cur["body"].append(ln)
    if cur["body"] or cur["heading"]:
        chunks.append((cur["line"], cur["heading"], "\n".join(cur["body"])))
    return chunks


def nfc(s: str) -> str:
    """Юникод-нормализация (NFC) — чтобы кириллица из разных источников сравнивалась."""
    return unicodedata.normalize("NFC", s)


def resolve_link(src_rel: str, href: str, known: set) -> str | None:
    """Разрешить md-ссылку из src_rel в путь из known (или None).

    Пробует относительный, нормализованный (../) и по базовому имени, если оно уникально.
    """
    href = nfc(href.split("#")[0].strip())
    if not href or not href.endswith(".md") or href.startswith(("http", "mailto:")):
        return None
    known = {nfc(k) for k in known}
    src_dir = Path(nfc(src_rel)).parent
    cands = []
    try:
        cands.append((src_dir / href).as_posix())
    except Exception:
        pass
    cands.append(href.lstrip("./"))
    cands.append(posixpath.normpath((src_dir / href).as_posix()))
    for c in cands:
        if c in known:
            return c
    base = Path(href).name
    hits = [r for r in known if r.endswith("/" + base) or r == base]
    return hits[0] if len(hits) == 1 else None
