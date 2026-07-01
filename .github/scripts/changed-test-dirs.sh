#!/usr/bin/env bash
#
# Когда:   шаг discover, только при changed_only-прогоне (PR).
# Зачем:   из всех тест-каталогов оставить только затронутые диффом ветки.
# Вход:    $1 = JSON-массив всех тест-каталогов (вывод discover-test-dirs.sh);
#          $2 = дефолтная ветка (по умолчанию main).
# Алгоритм:
#   base = merge-base(origin/<default>, HEAD); нет base → вернуть весь вход;
#   diff = git diff --name-only base...HEAD; по каждому файлу:
#     - игнор-список (доки/композ/Dockerfile/.github/мета) → не влияет, мимо;
#     - под тест-каталогом (самый длинный префикс)         → этот каталог;
#     - прочий код вне тест-каталогов                       → вернуть ВСЁ.
# Выход:   JSON затронутых тест-каталогов; [] если ничего (tests-джоб скипается);
#          весь вход, если base не вычислить или задет общий/корневой код.
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

# не-логика: изменения таких файлов не запускают тесты
is_ignored() {
  case "$1" in
    *.md|docs/*|.github/*) return 0;;
    Dockerfile|*/Dockerfile) return 0;;
    docker-compose*.yml|*.example.yml) return 0;;
    LICENSE|.gitignore|.editorconfig|.pre-commit-config.yaml) return 0;;
  esac
  return 1
}

git fetch --no-tags --depth=100 origin "$default" >/dev/null 2>&1 || true
base=$(git merge-base "origin/$default" HEAD 2>/dev/null || true)
# base не вычислить → фильтр не применим → всё
if [ -z "$base" ]; then printf '%s' "$all_json"; exit 0; fi

mapfile -t dirs    < <(json_items "$all_json")
mapfile -t changed < <(git diff --name-only "$base"...HEAD)

hit=()
for f in "${changed[@]}"; do
  is_ignored "$f" && continue

  # владелец = тест-каталог с самым длинным совпадающим префиксом пути
  # (корень "." владельцем не считаем: общий/корневой код → «вернуть ВСЁ» ниже)
  owner=""
  for d in "${dirs[@]}"; do
    [ "$d" = "." ] && continue
    case "$f" in "$d"/*) [ ${#d} -gt ${#owner} ] && owner="$d";; esac
  done

  if [ -n "$owner" ]; then
    hit+=("$owner")
  else
    printf '%s' "$all_json"; exit 0
  fi
done

if [ ${#hit[@]} -eq 0 ]; then
  printf '[]'
else
  mapfile -t uniq < <(printf '%s\n' "${hit[@]}" | sort -u)
  json_array "${uniq[@]}"
fi
