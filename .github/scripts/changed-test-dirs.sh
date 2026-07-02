#!/usr/bin/env bash
#
# Когда:   шаг discover, только при changed_only-прогоне (PR).
# Зачем:   выбрать тест-каталоги, затронутые диффом ветки.
# Вход:    $1 = JSON-массив всех тест-каталогов (вывод discover-test-dirs.sh);
#          $2 = дефолтная ветка (по умолчанию main).
# Алгоритм:
#   base = merge-base(origin/<default>, HEAD); нет base → вернуть весь вход;
#   diff = git diff --name-only base...HEAD; по каждому файлу:
#     - в списке исключений (доки/картинки/lint-CI-конфиги) → не влияет, пропуск;
#     - под services/<имя>/                                 → этот сервис;
#     - иначе (общий/корневой код)                          → весь вход (fail closed)..
# Выход:   JSON затронутых тест-каталогов; [] если ничего (tests-джоб скипается);
#          весь вход, если base не вычислить ЛИБО затронут общий/неизвестный код.
set -euo pipefail

all_json="$1"
default="${2:-main}"

# не влияют на тесты (glob через case, звёздочка покрывает слэши); нужно — дополняй
IGNORE_GLOBS=(
  'docs/*' '*/docs/*' '*.md' '*.mdx' '*.rst' '*.adoc' '*.txt'
  'LICENSE' 'LICENSE.*' 'NOTICE' 'AUTHORS' 'CODEOWNERS'
  '*.png' '*.jpg' '*.jpeg' '*.gif' '*.svg' '*.webp' '*.ico' '*.pdf'
  '.gitignore' '.gitattributes' '.editorconfig' '.gitmark/*' '.github/*'
  '.pre-commit-config.yaml' '.flake8' 'ruff.toml' '.ruff.toml'
  '.pylintrc' 'mypy.ini' '.mypy.ini' '.markdownlint*' '.yamllint*'
  'docker-compose.yml' 'docker-compose.yaml'
)

is_ignored() {
  local f="$1" pat
  for pat in "${IGNORE_GLOBS[@]}"; do
    case "$f" in $pat) return 0;; esac
  done
  return 1
}

json_items() { printf '%s' "$1" | grep -o '"[^"]*"' | sed -e 's/^"//' -e 's/"$//'; }
json_array() {
  local out="" item
  for item in "$@"; do
    item=${item//\\/\\\\}; item=${item//\"/\\\"}
    [ -z "$out" ] && out="\"$item\"" || out="$out,\"$item\""
  done
  printf '[%s]' "$out"
}

git fetch --no-tags --depth=100 origin "$default" >/dev/null 2>&1 || true
base=$(git merge-base "origin/$default" HEAD 2>/dev/null || true)
# base не вычислить → фильтр не применим → всё
if [ -z "$base" ]; then printf '%s' "$all_json"; exit 0; fi

mapfile -t dirs    < <(json_items "$all_json")
mapfile -t changed < <(git diff --name-only "$base"...HEAD)

hit=()
for f in "${changed[@]}"; do
  # исключения не влияют на тесты — пропускаем полностью
  is_ignored "$f" && continue

  # владелец = сервис с самым длинным совпадающим префиксом пути (корень не в счёт)
  owner=""
  for d in "${dirs[@]}"; do
    [ "$d" = "." ] && continue
    case "$f" in "$d"/*) [ ${#d} -gt ${#owner} ] && owner="$d";; esac
  done

  if [ -n "$owner" ]; then
    hit+=("$owner")
  else
    # неизвестный общий/корневой код → доказать локальность влияния нечем → гоняем всё
    printf '%s' "$all_json"; exit 0
  fi
done

if [ ${#hit[@]} -eq 0 ]; then
  printf '[]'
else
  mapfile -t uniq < <(printf '%s\n' "${hit[@]}" | sort -u)
  json_array "${uniq[@]}"
fi
