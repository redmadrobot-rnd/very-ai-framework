# Knowledge Base (GitMark) — онтология над кодом

Документацию проекта ведём как **онтологическую базу знаний**: каждый несущий
документ — типизированный объект (`node_type`) со свойствами (frontmatter) и
**типизированными связями** на другие доки и на код (подход — как Palantir Ontology,
но над документацией). Так знание остаётся навигируемым и проверяемым линтером.

**Source of truth — markdown** под `docs/`; производный индекс поиска (`.gitmark/`)
постоянно регенерируется и в git не хранится.

> `docs/` — это KB **прикладного проекта**, который использует фреймворк. Сам фреймворк
> в `docs/` ничего не кладёт.

## Части

| Компонент | Что это |
|---|---|
| [`docs/gitmark/ontology.md`](docs/gitmark/ontology.md) | Спека модели: словари `node_type`/`status`, таблица типов связей, инварианты |
| `.codex/skills/kb-search` или [`.claude/skills/kb-search`](.claude/skills/kb-search/SKILL.md) | Поиск по KB через GitMark CLI (`bm25`/`trigram`/`fuzzy`) |
| `.codex/skills/kb-maintain` или [`.claude/skills/kb-maintain`](.claude/skills/kb-maintain/SKILL.md) | Правила ведения KB: добавление/правка/перенос доков, frontmatter, связи |
| `.codex/commands/kb-doc.md` или `.claude/commands/kb-doc.md` (`/kb-doc`) | Создать/обновить один документ по теме (обёртка над `kb-maintain`) |
| `.codex/commands/kb-build.md` или `.claude/commands/kb-build.md` (`/kb-build`) | Построить всю KB репозитория фан-аутом агентов-кураторов |

## CLI (GitMark)

```bash
G="bash .github/scripts/gitmark.sh"
$G search "<query>" -k 8   # поиск
$G index                   # пересобрать индекс
$G lint                    # проверить инварианты I1–I6
$G stat                    # покрытие KB
```

Инварианты (`I1`–`I6`), которые проверяет `lint`, и контролируемые словари — в
[`docs/gitmark/ontology.md`](docs/gitmark/ontology.md) и в скилле `kb-maintain`.

## Принципы

- **Markdown — source of truth.** Правим `.md`, индекс не трогаем.
- **`.gitmark/` не коммитим** — это gitignored перестраиваемый кэш.
- **Не плодим дубли** — перед созданием дока ищем существующий и правим его.
