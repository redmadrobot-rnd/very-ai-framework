#!/usr/bin/env python3
"""Детерминированный рендер overview из .github/overview/ -> site/.

LLM здесь НЕ участвует: контент (onepager/hld/changelog/*.puml) уже подготовлен агентом.
Собираются ДВЕ строгие тёмные страницы-onepager'а по аудитории:

  index.html — Overview (менее техническая): суть, возможности, факты, refs, owner.
  tech.html  — Tech (техническая): архитектура+диаграммы, интерфейсы, запуск, стек, changelog.

Вход   : .github/overview/{onepager.md, hld.md, changelog.md, architecture/*.puml, template/}
Выход  : <out>/{index.html, tech.html, style.css, *.svg}
Зависимости: jinja2, markdown, pyyaml + PlantUML (команда `plantuml` или $PLANTUML_CMD).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from html import escape
from pathlib import Path

import markdown as md
import yaml
from jinja2 import Template


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Разбить `--- yaml --- body` на (meta, body). Без фронтматтера -> ({}, text)."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return yaml.safe_load(parts[1]) or {}, parts[2].strip()
    return {}, text.strip()


def md2html(text: str) -> str:
    return md.markdown(text, extensions=["extra", "sane_lists", "tables"])


def bullets(items: list) -> str:
    return "<ul>" + "".join(f"<li>{escape(str(x))}</li>" for x in items) + "</ul>"


def linklist(items: list, key: str = "label") -> str:
    rows = []
    for it in items:
        label, url = escape(str(it.get(key, ""))), it.get("url", "")
        rows.append(f'<li>{label}: <a href="{escape(url)}">{escape(url)}</a></li>' if url else f"<li>{label}</li>")
    return "<ul>" + "".join(rows) + "</ul>"


def section(title: str, body: str, anchor: str = "") -> str:
    if not body:
        return ""
    aid = f' id="{anchor}"' if anchor else ""
    return f"<section{aid}><h2>{escape(title)}</h2>{body}</section>"


def side_group(title: str, body: str) -> str:
    if not body:
        return ""
    head = f"<h2>{escape(title)}</h2>" if title else ""
    return f'<div class="group">{head}{body}</div>'


def hero(meta: dict, subtitle: str = "") -> str:
    h = ['<section class="hero" id="overview">']
    h.append(f"<h1>{escape(str(meta.get('title', 'Project')))}</h1>")
    tagline = subtitle or meta.get("tagline", "")
    if tagline:
        h.append(f'<p class="tagline">{escape(str(tagline))}</p>')
    badges = []
    if meta.get("status"):
        badges.append(f'<span class="badge status">{escape(str(meta["status"]))}</span>')
    if meta.get("version"):
        badges.append(f'<span class="badge">{escape(str(meta["version"]))}</span>')
    if meta.get("updated"):
        badges.append(f'<span class="badge">updated {escape(str(meta["updated"]))}</span>')
    if badges:
        h.append('<div class="badges">' + "".join(badges) + "</div>")
    h.append("</section>")
    return "\n".join(h)


def cols(main_parts: list, side_parts: list) -> str:
    main_html = "\n".join(filter(None, main_parts))
    side_html = "".join(filter(None, side_parts))
    side = f'<div class="side"><section>{side_html}</section></div>' if side_html else ""
    return f'<div class="cols"><div class="main">{main_html}</div>{side}</div>'


def techstack_html(techstack: dict) -> str:
    g = []
    for group, items in techstack.items():
        label = escape(str(group))
        if isinstance(items, dict):
            if items.get("note"):
                label += f' <span class="vibe">({escape(str(items["note"]))})</span>'
            items = items.get("items", [])
        g.append(f'<div class="ts"><h3>{label}</h3>{bullets(items)}</div>')
    return "".join(g)


def changelog_html(text: str) -> str:
    if not text.strip():
        return ""
    chunks = [c for c in re.split(r"(?m)^##\s+", text) if c.strip()]
    blocks = []
    for i, chunk in enumerate(chunks):
        head, _, rest = chunk.partition("\n")
        latest = " latest" if i == 0 else ""
        blocks.append(
            f'<div class="release{latest}"><div class="ver">{escape(head.strip())}</div>{md2html(rest.strip())}</div>'
        )
    return "".join(blocks)


def render_puml(arch_dir: Path, out: Path) -> list[tuple[str, str]]:
    """Каждый .puml -> .svg в out/. Возвращает [(svg_filename, caption)]."""
    cmd = os.environ.get("PLANTUML_CMD", "plantuml")
    diagrams = []
    for puml in sorted(arch_dir.glob("*.puml")):
        try:
            subprocess.run([cmd, "-tsvg", "-charset", "UTF-8", "-o", str(out.resolve()), str(puml)], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"WARN: не удалось отрендерить {puml.name}: {e}", file=sys.stderr)
            continue
        svg = out / (puml.stem + ".svg")
        if svg.exists():
            diagrams.append((svg.name, puml.stem.replace("_", " ")))
    return diagrams


def page_overview(meta: dict, body: str) -> str:
    main = [
        f'<section><p class="lead">{escape(str(meta["summary"]))}</p></section>' if meta.get("summary") else "",
        section("Возможности", bullets(meta["capabilities"]) if meta.get("capabilities") else ""),
        section("Метрики", bullets(meta["metrics"]) if meta.get("metrics") else ""),
        md2html(body) if body else "",
    ]
    side = [
        side_group("Окружения", linklist(meta["environments"], "name") if meta.get("environments") else ""),
        side_group("Refs", linklist(meta["refs"]) if meta.get("refs") else ""),
        side_group("Owner", f"<div>{escape(str(meta['owner']))}</div>" if meta.get("owner") else ""),
    ]
    return f'<div class="wrap">{hero(meta)}{cols(main, side)}</div>'


def page_tech(meta: dict, hld_md: str, diagrams: list[tuple[str, str]], changelog: str) -> str:
    arch = md2html(hld_md) if hld_md else ""
    for svg, caption in diagrams:
        arch += (
            f'<div class="diagram"><img src="{escape(svg)}" alt="{escape(caption)}">'
            f'<div class="caption">{escape(caption)}</div></div>'
        )
    main = [
        section("Архитектура", arch),
        section("Интерфейсы", bullets(meta["interfaces"]) if meta.get("interfaces") else ""),
        section("Запуск / точки входа", bullets(meta["run"]) if meta.get("run") else ""),
        section("Changelog", changelog_html(changelog)),
        section("Важно знать", bullets(meta["caveats"]) if meta.get("caveats") else ""),
    ]
    side = [
        side_group("Стек", techstack_html(meta["techstack"])) if meta.get("techstack") else "",
        side_group("Окружения", linklist(meta["environments"], "name") if meta.get("environments") else ""),
        side_group("Refs", linklist(meta["refs"]) if meta.get("refs") else ""),
    ]
    sub = f"{meta.get('title', '')} — техническое устройство"
    return f'<div class="wrap">{hero(meta, subtitle=sub)}{cols(main, side)}</div>'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=".github/overview")
    ap.add_argument("--out", default=".github/overview/site")
    ap.add_argument("--release", default="dev")
    ap.add_argument("--generated", default="")
    args = ap.parse_args()

    src, out = Path(args.src), Path(args.out)
    tpl_dir = src / "template"
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy(tpl_dir / "style.css", out / "style.css")
    layout = Template((tpl_dir / "layout.html").read_text(encoding="utf-8"))

    meta, body = split_frontmatter((src / "onepager.md").read_text(encoding="utf-8"))
    site_name = meta.get("title", "Project")
    diagrams = render_puml(src / "architecture", out) if (src / "architecture").is_dir() else []
    hld_md = (src / "hld.md").read_text(encoding="utf-8") if (src / "hld.md").exists() else ""
    changelog = (src / "changelog.md").read_text(encoding="utf-8") if (src / "changelog.md").exists() else ""

    pages = [
        ("index", "overview", page_overview(meta, body)),
        ("tech", "tech", page_tech(meta, hld_md, diagrams, changelog)),
    ]
    for page, page_title, content in pages:
        html = layout.render(
            site_name=site_name,
            page=page,
            page_title=page_title,
            content=content,
            release=args.release,
            generated=args.generated,
        )
        (out / f"{page}.html").write_text(html, encoding="utf-8")
    print(f"OK: overview -> {out} (2 pages, {len(diagrams)} diagrams)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
