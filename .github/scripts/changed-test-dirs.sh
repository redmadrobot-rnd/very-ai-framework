#!/usr/bin/env bash
#
# ЧТО ДЕЛАЕТ: из полного списка тест-каталогов оставляет только затронутые диффом ветки (PR)
#             или релиза относительно прода. Так CI гоняет тесты не всего проекта, а только
#             изменённых сервисов.
# Вход:    $1 = JSON-массив всех тест-каталогов (вывод discover-test-dirs.sh);
#          $2 = дефолтная ветка (по умолчанию main);
#          $3 = точка отсчёта, опционально: base-sha PR'а (github.event.pull_request.base.sha)
#               или коммит прода из .deployed (release.yml);
#          $4 = как сравнивать: merge-base (PR, по умолчанию) | tree (релиз).
#               tree — дерево базы против дерева HEAD напрямую: прод после хотфикса на
#               release/* не предок main, и от общего предка правка прода, которую релиз
#               откатывает, не видна. В tree правки под services/<имя>/ не фильтруются по
#               исключениям: *.txt/*.md там бывают промптами, которые едут в образ.
# Алгоритм:
#   base = $3, если он есть в истории (иначе merge-base(origin/<default>, HEAD)); без $3
#     фильтр зависит от того, где сейчас origin/<default>: если PR смёржили ДО этого шага,
#     merge-base == HEAD, дифф пуст — тесты «зелёные», не запустившись;
#   нет base → вернуть весь вход;
#   diff = git diff --name-only base...HEAD (tree: base HEAD; не посчитать → весь вход); по каждому файлу:
#     - в списке исключений (доки/картинки/lint-CI-конфиги) → не влияет, пропуск;
#     - под services/<имя>/  → тест-каталоги этого сервиса (d == services/<имя>
#                              ИЛИ d начинается с services/<имя>/);
#                              сервис без тест-каталога → тестировать нечего, пропуск;
#     - вне services/* (общий/корневой код)               → весь вход (fail closed).
# Выход:   JSON затронутых тест-каталогов; [] если ничего (tests-джоб скипается);
#          весь вход, если base не вычислить ЛИБО затронут корневой/общий код.
set -euo pipefail

all_json="$1"
default="${2:-main}"
base_sha="${3:-}"
mode="${4:-merge-base}"

# Папка сервисов. Дефолт services; переопределяется env SERVICES_DIR (в CI — из vars.SERVICES_DIR).
SERVICES_DIR="${SERVICES_DIR:-services}"

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

# база сравнения: явная точка отсчёта (base PR / коммит прода), иначе точка, откуда ветка
# отошла от main; не вычислить → гоним всё
base=""
if [ -n "$base_sha" ]; then
  # тянем, только если коммита нет: --depth на полном клоне укоротил бы историю
  git cat-file -e "$base_sha^{commit}" >/dev/null 2>&1     || git fetch --no-tags --depth=100 origin "$base_sha" >/dev/null 2>&1 || true
  git cat-file -e "$base_sha^{commit}" >/dev/null 2>&1 && base="$base_sha"
fi
if [ -z "$base" ]; then
  git fetch --no-tags --depth=100 origin "$default" >/dev/null 2>&1 || true
  base=$(git merge-base "origin/$default" HEAD 2>/dev/null || true)
fi
if [ -z "$base" ]; then printf '%s' "$all_json"; exit 0; fi

range="$base...HEAD"
[ "$mode" = tree ] && range="$base HEAD"
# дифф не посчитать (обрезанная история и т.п.) → гоним всё, а не пустую матрицу
# shellcheck disable=SC2086
if ! diff_out=$(git diff --name-only $range); then printf '%s' "$all_json"; exit 0; fi

# dirs = все тест-каталоги; changed = изменённые файлы
mapfile -t dirs    < <(json_items "$all_json")
mapfile -t changed < <(printf '%s' "$diff_out" | sed '/^$/d')

hit=()
for f in "${changed[@]}"; do
  # исключения не влияют на тесты (в tree — кроме файлов сервисов)
  if [ "$mode" != tree ] || [[ "$f" != "$SERVICES_DIR"/*/* ]]; then
    is_ignored "$f" && continue
  fi

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
