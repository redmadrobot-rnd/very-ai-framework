#!/usr/bin/env bash
#
# ЧТО ДЕЛАЕТ: ищет все каталоги, где есть тесты (корень проекта + каждый сервис).
#             По этому списку CI дальше решает, где запускать pytest.
# Вход:    нет (работает от корня репозитория).
# Алгоритм:
#   тест-каталог = папка с pyproject.toml, где есть [project] или [tool.pytest.*];
#   берём корень (.) + по одному ближайшему в каждом services/<имя>/.
# Выход:   JSON-массив путей тест-каталогов, напр. [".","services/auth"].
set -euo pipefail

# Папка, в которой лежат сервисы с pytests.
SERVICES_DIR="services"

# является ли этот pyproject.toml тест-каталогом (есть [project] или pytest-конфиг)
is_test_dir() { grep -qE '^\[(project|tool\.pytest)' "$1" 2>/dev/null; }

# собрать JSON-массив строк вручную
json_array() {
  local out="" item
  for item in "$@"; do
    item=${item//\\/\\\\}; item=${item//\"/\\\"}
    [ -z "$out" ] && out="\"$item\"" || out="$out,\"$item\""
  done
  printf '[%s]' "$out"
}

# найти ближайший к корню тест-pyproject внутри каталога и напечатать его папку
nearest_test_dir() {
  local manifest
  while IFS= read -r manifest; do
    if is_test_dir "$manifest"; then dirname "$manifest"; return; fi
  done < <(
    find "$1" -path '*/.venv' -prune -o -path '*/node_modules' -prune -o \
      -name pyproject.toml -print 2>/dev/null \
    | awk -F/ '{print NF, $0}' | sort -k1,1n -k2 | cut -d' ' -f2-
  )
}

dirs=()

# 1) корневое окружение — если сам корень репозитория является тест-каталогом
if [ -f pyproject.toml ] && is_test_dir pyproject.toml; then
  dirs+=(".")
fi

# 2) по одному тест-каталогу на каждый сервис в SERVICES_DIR
if [ -d "$SERVICES_DIR" ]; then
  for svc in "$SERVICES_DIR"/*/; do
    d=$(nearest_test_dir "$svc"); [ -n "$d" ] && dirs+=("$d")
  done
fi

# печатаем итоговый список
json_array ${dirs[@]+"${dirs[@]}"}
