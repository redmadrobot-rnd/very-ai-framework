#!/usr/bin/env bash
#
# Когда:   шаг discover, только при changed_only-прогоне (PR).
# Зачем:   выбрать тест-каталоги, затронутые диффом ветки.
# Вход:    $1 = JSON-массив всех тест-каталогов (вывод discover-test-dirs.sh);
#          $2 = дефолтная ветка (по умолчанию main).
# Алгоритм:
#   base = merge-base(origin/<default>, HEAD); нет base → вернуть весь вход;
#   diff = git diff --name-only base...HEAD; по каждому файлу:
#     - docs/** или *.md             → не влияет на тесты, пропускаем;
#     - под services/<имя>/          → этот сервис;
#     - иначе (корневой/общий код)    → корневое окружение "." (если оно есть).
#   Незатронутые сервисы не попадают; «все сервисы скопом» как исход отсутствует.
# Выход:   JSON затронутых тест-каталогов; [] если ничего (tests-джоб скипается);
#          весь вход, если base не вычислить.
set -euo pipefail

all_json="$1"
default="${2:-main}"

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

# есть ли корневое окружение среди обнаруженных
has_root=0
for d in "${dirs[@]}"; do [ "$d" = "." ] && has_root=1; done

hit=()
for f in "${changed[@]}"; do
  # доки/markdown не влияют на тесты — пропускаем полностью
  case "$f" in docs/*|*.md) continue;; esac

  # владелец = сервис с самым длинным совпадающим префиксом пути (корень не в счёт)
  owner=""
  for d in "${dirs[@]}"; do
    [ "$d" = "." ] && continue
    case "$f" in "$d"/*) [ ${#d} -gt ${#owner} ] && owner="$d";; esac
  done

  if [ -n "$owner" ]; then
    hit+=("$owner")
  elif [ "$has_root" = "1" ]; then
    hit+=(".")   # корневой/общий/док-файл → корневое окружение
  fi
done

if [ ${#hit[@]} -eq 0 ]; then
  printf '[]'
else
  mapfile -t uniq < <(printf '%s\n' "${hit[@]}" | sort -u)
  json_array "${uniq[@]}"
fi
