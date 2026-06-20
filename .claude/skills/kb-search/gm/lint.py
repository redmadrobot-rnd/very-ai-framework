"""Проверка инвариантов онтологии (см. docs/gitmark/ontology.md).

Набор инвариантов — в INVARIANTS ниже (единый источник истины: код → описание).
"""
from __future__ import annotations

import re
from pathlib import Path

from .core import KB_DIR, LINK_RE, iter_md, kb_subpath, nfc, resolve_link

# Подкаталоги БЗ, где документ считается «несущим» (требует frontmatter с node_type).
BEARING_DIRS = ("reference", "ops", "plans", "decisions", "services")

# Инварианты онтологии: код → описание. Единый источник истины — отсюда же
# берётся справка CLI. Сами проверки реализованы в cmd_lint и помечают находки кодом.
INVARIANTS = {
    "I1": "несущий документ имеет frontmatter с валидным node_type",
    "I2": "node_type / status — в своих словарях",
    "I3": "нет сирот (несущий тип имеет ≥1 входящую или исходящую связь)",
    "I4": "нет битых md-ссылок",
    "I5": "у каждой docs/-папки есть README.md (индекс)",
    "I6": "цель supersedes помечена deprecated|archived",
}

# Контролируемые словари (фиксированы — ядро модели). `service` НЕ контролируется:
# это свободное поле, агент-куратор сам решает, к какому компоненту отнести документ.
NODE_TYPES = {"service", "reference", "runbook", "gotcha", "decision",
              "plan", "guide", "report", "index", "memory"}

STATUSES = {"active", "draft", "deprecated", "archived"}

LOAD_BEARING = {"service", "reference", "runbook", "plan", "decision"}

FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`]*`")


def _blank(m: re.Match) -> str:
    """Заменить совпадение пробелами, сохранив переносы строк и длину.

    Длино-сохраняющая замена нужна, чтобы смещения в очищенном тексте совпадали с
    исходными — иначе номера строк для ссылок (I4) поедут.
    """
    return "".join("\n" if ch == "\n" else " " for ch in m.group(0))


def strip_code(text: str) -> str:
    """Вычистить fenced ``` и inline `code` — чтобы не ловить ссылки-примеры из кода.

    Заменяет на пробелы той же длины (переносы сохраняются) → позиции не сбиваются.
    """
    return INLINE_CODE_RE.sub(_blank, FENCE_RE.sub(_blank, text))


def _line_at(text: str, pos: int) -> int:
    """1-based номер строки для смещения pos."""
    return text.count("\n", 0, pos) + 1


def _exists_on_disk(root: Path, src_rel: str, href: str) -> bool:
    """Существует ли цель ссылки на диске (file-relative или root-relative).

    Нужно, чтобы отличать реально битую ссылку (файла нет) от ссылки на существующий
    файл ВНЕ `docs/` (БЗ сужена до docs/, но такой файл не «битый» — он просто вне scope).
    """
    h = href.split("#")[0].strip()
    if not h:
        return True
    for cand in ((root / Path(src_rel).parent / h), (root / h.lstrip("./"))):
        try:
            if cand.exists():
                return True
        except OSError:
            pass
    return False


def _fm_line(text: str, key: str) -> int:
    """Строка ключа `key:` во frontmatter (1-based); 1, если не найден."""
    m = FM_RE.match(text)
    if not m:
        return 1
    base = _line_at(text, m.start(1))
    for i, ln in enumerate(m.group(1).split("\n")):
        if re.match(rf"\s*{re.escape(key)}\s*:", ln):
            return base + i
    return 1


def _parse_val(val: str):
    """Значение frontmatter: инлайн-список `[a, b]` → list, иначе скаляр (без кавычек)."""
    if val.startswith("[") and val.endswith("]"):
        return [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
    return val.strip("'\"")


def _indent(raw: str) -> int:
    """Ширина ведущего отступа (пробелы/табы)."""
    return len(raw) - len(raw.lstrip(" \t"))


def parse_frontmatter(text: str) -> dict | None:
    """Мини-парсер YAML-frontmatter (stdlib, без pyyaml).

    Поддерживает: скаляры, плоские списки (инлайн `[..]` и блочные `- ..`) и
    вложенное отображение под `links:` — как инлайн-списки (`documents: [a, b]`),
    так и блочные (`depends_on:` + строкой ниже `  - a`).
    """
    m = FM_RE.match(text)
    if not m:
        return None
    fm: dict = {}
    cur_key = None   # ключ верхнего уровня, чьё значение — list или dict
    cur_sub = None   # подключ внутри map-блока (напр. links.depends_on), чьё значение — list
    for raw in m.group(1).split("\n"):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = _indent(raw)
        stripped = raw.strip()
        # элемент блочного списка "- x": во вложенный список под map либо в список верхнего уровня
        if stripped.startswith("- "):
            item = stripped[2:].strip().strip("[]'\"")
            if cur_sub is not None and isinstance(fm.get(cur_key), dict) and isinstance(fm[cur_key].get(cur_sub), list):
                fm[cur_key][cur_sub].append(item)
            elif isinstance(fm.get(cur_key), list):
                fm[cur_key].append(item)
            continue
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key, val = key.strip(), val.strip()
        # вложенная пара "subkey: ..." под map-блоком (напр. links:)
        if indent > 0 and isinstance(fm.get(cur_key), dict):
            fm[cur_key][key] = _parse_val(val) if val else []
            cur_sub = None if val else key            # пустое значение → дальше блочные "- .."
            continue
        # ключ верхнего уровня
        cur_sub = None
        if not val:                                   # начало вложенного блока/списка
            cur_key = key
            fm[key] = {} if key == "links" else []
            continue
        cur_key = None
        fm[key] = _parse_val(val)
    return fm


def _fm_link_targets(fm: dict | None):
    """Типизированные связи из frontmatter `links:` → [(link_type, target), ...]."""
    links = (fm or {}).get("links")
    if not isinstance(links, dict):
        return []
    out = []
    for ltype, targets in links.items():
        if isinstance(targets, str):
            targets = [targets]
        for t in targets or []:
            if isinstance(t, str) and t.strip():
                out.append((ltype, t.strip()))
    return out


def cmd_lint(root: Path, paths: list | None = None) -> dict:
    """Проверить инварианты I1–I6. Возвращает {issues, checked}.

    issues — список (level, code, path, line, msg); level ∈ {ERR, WARN}. line — 1-based
    номер строки (0 = находка уровня файла/папки, без конкретной строки). Подсчёт и
    фильтрация — на стороне потребителя (issues — единственный источник правды).
    """
    docs = list(iter_md(root))
    known = {nfc(p.relative_to(root).as_posix()) for p in docs}
    # граф связей: кто на кого ссылается (для I3 — сироты)
    out_links: dict = {}
    in_links: dict = {}
    ext_out: set = set()   # документы со ссылкой на существующий файл ВНЕ docs/ (тоже связь)
    issues = []  # (level, code, path, line, msg)
    fm_cache = {}
    text_cache = {}
    sel = set(paths) if paths else None

    for p in docs:
        rel = p.relative_to(root).as_posix()
        try:
            text = p.read_text("utf-8", errors="replace")
        except Exception:
            continue
        fm_cache[rel] = parse_frontmatter(text)
        text_cache[rel] = text
        outs = set()
        stripped = strip_code(text)
        for mt in LINK_RE.finditer(stripped):
            href = mt.group(1)
            h0 = href.split("#")[0]
            tgt = resolve_link(rel, href, known)
            if tgt:                                    # связь внутри docs/
                outs.add(tgt)
                in_links.setdefault(tgt, set()).add(rel)
            elif not h0 or h0.startswith(("http", "mailto:")):
                pass                                   # якорь/внешний URL — не файл
            elif _exists_on_disk(root, rel, href):
                ext_out.add(rel)                       # существует, но вне docs/ — это связь, не сирота
            elif h0.endswith(".md"):
                issues.append(("ERR", "I4", rel, _line_at(stripped, mt.start(1)), f"битая ссылка → {href}"))
        # типизированные связи из frontmatter — тоже часть графа (I3) и проверяются на битость (I4)
        for ltype, tgt in _fm_link_targets(fm_cache[rel]):
            t = resolve_link(rel, tgt, known)
            if t:                                      # ссылка на док внутри БЗ
                outs.add(t)
                in_links.setdefault(t, set()).add(rel)
            elif _exists_on_disk(root, rel, tgt):
                ext_out.add(rel)                       # ссылка на код/файл вне БЗ — это тоже связь
            else:
                issues.append(("ERR", "I4", rel, _fm_line(text, ltype), f"битая ссылка в links.{ltype} → {tgt}"))
        out_links[rel] = outs

    # README на каждую папку БЗ (I5) — находка уровня папки, строки нет
    docs_dirs = {p.parent for p in docs if p.relative_to(root).as_posix().startswith(KB_DIR + "/")}
    for d in sorted(docs_dirs):
        if not (d / "README.md").exists():
            issues.append(("WARN", "I5", d.relative_to(root).as_posix() + "/", 0, "нет README.md (индекс папки)"))

    for p in docs:
        rel = p.relative_to(root).as_posix()
        if sel and rel not in sel:
            continue
        if not rel.startswith(KB_DIR + "/"):
            continue
        fm = fm_cache.get(rel)
        text = text_cache.get(rel, "")
        nt = (fm or {}).get("node_type")
        # I1 — несущий документ без frontmatter/типа
        looks_bearing = kb_subpath(rel).split("/")[0] in BEARING_DIRS
        if not fm or not nt:
            if looks_bearing and Path(rel).name != "README.md":
                issues.append(("ERR", "I1", rel, 1, "нет frontmatter с node_type"))
            continue
        # I2 — значения в словарях (service не контролируется — свободное поле)
        if nt not in NODE_TYPES:
            issues.append(("ERR", "I2", rel, _fm_line(text, "node_type"), f"node_type='{nt}' вне словаря"))
        st = fm.get("status")
        if st and st not in STATUSES:
            issues.append(("WARN", "I2", rel, _fm_line(text, "status"), f"status='{st}' вне словаря"))
        # I3 — сироты (несущий тип без входящих/исходящих связей) — уровень документа
        if nt in LOAD_BEARING:
            # связи из frontmatter уже учтены в out_links/in_links/ext_out (первый проход),
            # поэтому битые ссылки больше НЕ маскируют сироту (раньше любой непустой links: глушил I3).
            has_link = bool(out_links.get(rel)) or bool(in_links.get(rel)) or (rel in ext_out)
            if not has_link:
                issues.append(("WARN", "I3", rel, 1, "сирота — нет связей (ни in, ни out)"))
        # I6 — supersedes-цель должна быть deprecated/archived
        links_fm = fm.get("links") if isinstance(fm.get("links"), dict) else {}
        for tgt in (links_fm.get("supersedes") or []):
            t = resolve_link(rel, tgt, known)
            if t:
                tfm = fm_cache.get(t) or {}
                if tfm.get("status") not in ("deprecated", "archived"):
                    issues.append(("WARN", "I6", rel, _fm_line(text, "supersedes"),
                                   f"supersedes {tgt}, но он не deprecated/archived"))

    return {"issues": issues, "checked": len(docs)}
