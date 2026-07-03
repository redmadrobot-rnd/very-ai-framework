#!/usr/bin/env bash
#
# ЧТО ДЕЛАЕТ: из полного списка тест-каталогов оставляет только затронутые диффом ветки (PR).
#             Так CI гоняет тесты не всего проекта, а только изменённых сервисов.
#
# Когда:   второй шаг discover, только при changed_only-прогоне (PR).
# Вход:    $1 = JSON всех тест-каталогов; $2 = ветка сравнения (по умолчанию main).
# Выход:   JSON затронутых тест-каталогов ([] — ничего; весь вход — общий код или нет базы).
set -euo pipefail

all_json="$1"
default="${2:-main}"

# Папка, в которой лежат сервисы. Поменялась раскладка репозитория — правим здесь.
SERVICES_DIR="services"

# файлы, которые не влияют на тесты — их правки игнорируем
IGNORE_GLOBS=(
  'docs/*' '*/docs/*' '*.md' '*.mdx' '*.rst' '*.adoc' '*.txt'
  'LICENSE' 'LICENSE.*' 'NOTICE' 'AUTHORS' 'CODEOWNERS'
  '*.png' '*.jpg' '*.jpeg' '*.gif' '*.svg' '*.webp' '*.ico' '*.pdf'
  '.gitignore' '.gitattributes' '.editorconfig' '.gitmark/*' '.github/*'
  '.pre-commit-config.yaml' '.flake8' 'ruff.toml' '.ruff.toml'
  '.pylintrc' 'mypy.ini' '.mypy.ini' '.markdownlint*' '.yamllint*'
  'docker-compose.yml' 'docker-compose.yaml'
)

# попадает ли файл в список исключений
is_ignored() {
  local f="$1" pat
  for pat in "${IGNORE_GLOBS[@]}"; do
    case "$f" in $pat) return 0;; esac
  done
  return 1
}

# JSON-массив ↔ построчный список (без jq)
json_items() { printf '%s' "$1" | grep -o '"[^"]*"' | sed -e 's/^"//' -e 's/"$//'; }
json_array() {
  local out="" item
  for item in "$@"; do
    item=${item//\\/\\\\}; item=${item//\"/\\\"}
    [ -z "$out" ] && out="\"$item\"" || out="$out,\"$item\""
  done
  printf '[%s]' "$out"
}

# база сравнения = точка, откуда ветка отошла от main; не вычислить → гоним всё
git fetch --no-tags --depth=100 origin "$default" >/dev/null 2>&1 || true
base=$(git merge-base "origin/$default" HEAD 2>/dev/null || true)
if [ -z "$base" ]; then printf '%s' "$all_json"; exit 0; fi

# dirs = все тест-каталоги; changed = изменённые файлы ветки
mapfile -t dirs    < <(json_items "$all_json")
mapfile -t changed < <(git diff --name-only "$base"...HEAD)

hit=()
for f in "${changed[@]}"; do
  is_ignored "$f" && continue

  case "$f" in
    "$SERVICES_DIR"/*/*)
      # файл сервиса → берём тест-каталоги ЭТОГО сервиса.
      # сервис определяем по сегменту services/<имя>, а не по полному пути:
      # код лежит в services/<имя>/workdir/, а Dockerfile — в services/<имя>/.
      rest=${f#"$SERVICES_DIR"/}
      name=${rest%%/*}
      svc="$SERVICES_DIR/$name"
      for d in "${dirs[@]}"; do
        [ "$d" = "." ] && continue
        case "$d" in
          "$svc"|"$svc"/*) hit+=("$d");;
        esac
      done
      # сервис без тест-каталога → тестировать нечего, идём дальше
      ;;
    *)
      # файл вне services/ → общий/корневой код → гоним всё (fail-closed)
      printf '%s' "$all_json"; exit 0
      ;;
  esac
done

# результат: [] если ничего не затронуто, иначе список без дублей
if [ ${#hit[@]} -eq 0 ]; then
  printf '[]'
else
  mapfile -t uniq < <(printf '%s\n' "${hit[@]}" | sort -u)
  json_array "${uniq[@]}"
fi
