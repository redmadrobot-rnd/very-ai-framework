#!/usr/bin/env bash
#
# Фильтрует список окружений, оставляя только затронутые изменениями ветки.
# Отдельный слой поверх discover-envs.sh: базовый список не знает про git.
#
#   $1 = JSON-массив всех окружений (вывод discover-envs.sh)
#   $2 = git base ref (например merge-base с main); пусто = фильтрация отключена
# Печатает JSON-массив затронутых окружений в stdout.
#
# Правило:
#   - файл под services/<env>/ → затронут этот сервис;
#   - изменён общий/корневой файл (вне сервисных окружений, не .md и не docs/) →
#     перестраховка: гоняем ВСЁ (нет графа зависимостей — не угадываем влияние);
#   - изменены только доки → ничего.
set -euo pipefail

all_json="$1"
base="${2:-}"

# Парсинг/печать JSON-массива строк без зависимости от jq.
json_items() { printf '%s' "$1" | grep -o '"[^"]*"' | sed -e 's/^"//' -e 's/"$//'; }
json_array() {
  local out="" item
  for item in "$@"; do
    item=${item//\\/\\\\}; item=${item//\"/\\\"}
    [ -z "$out" ] && out="\"$item\"" || out="$out,\"$item\""
  done
  printf '[%s]' "$out"
}

# нет валидной базы → не фильтруем, возвращаем всё
if [ -z "$base" ] || ! git rev-parse --verify "$base" >/dev/null 2>&1; then
  printf '%s' "$all_json"
  exit 0
fi

mapfile -t envs    < <(json_items "$all_json")
mapfile -t changed < <(git diff --name-only "$base"...HEAD)

affected=()
for f in "${changed[@]}"; do
  # доки не влияют на тесты
  case "$f" in *.md|docs/*) continue;; esac

  owner=""
  for env in "${envs[@]}"; do
    [ "$env" = "." ] && continue
    case "$f" in "$env"/*) owner="$env";; esac
  done

  if [ -n "$owner" ]; then
    affected+=("$owner")
  else
    # файл вне сервисных окружений → потенциально общий → гоняем всё
    printf '%s' "$all_json"
    exit 0
  fi
done

if [ ${#affected[@]} -eq 0 ]; then
  printf '[]'
else
  mapfile -t uniq < <(printf '%s\n' "${affected[@]}" | sort -u)
  json_array "${uniq[@]}"
fi
