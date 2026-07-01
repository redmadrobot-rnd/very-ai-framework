#!/usr/bin/env bash
#
# Когда:   шаг discover tests-джоба, до вычисления затронутого.
# Зачем:   найти все тест-каталоги репо, не завися от их числа и имён.
# Вход:    нет (работает от корня репозитория).
# Алгоритм:
#   тест-каталог = папка с pyproject.toml, где есть [project] или [tool.pytest.*];
#   ищем в: корне (.), каждом services/<имя>/, каждом прочем верхнеуровневом
#   каталоге (e2e/, integration/, tests/ …). .venv/node_modules/docs/скрытые — мимо.
# Выход:   JSON-массив путей тест-каталогов, напр. [".","services/auth","e2e"].
set -euo pipefail

# тест-каталог = pyproject с [project] или pytest-конфигом ([tool.pytest.*])
is_test_dir() { grep -qE '^\[(project|tool\.pytest)' "$1" 2>/dev/null; }

# компактный JSON-массив строк (без зависимости от jq)
json_array() {
  local out="" item
  for item in "$@"; do
    item=${item//\\/\\\\}; item=${item//\"/\\\"}
    [ -z "$out" ] && out="\"$item\"" || out="$out,\"$item\""
  done
  printf '[%s]' "$out"
}

# ближайший (по глубине) подходящий pyproject под каталогом → печатает его dir, иначе ничего
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

# корень
if [ -f pyproject.toml ] && is_test_dir pyproject.toml; then
  dirs+=(".")
fi

# сервисы: по одному тест-каталогу на services/<имя>/
if [ -d services ]; then
  for svc in services/*/; do
    d=$(nearest_test_dir "$svc"); [ -n "$d" ] && dirs+=("$d")
  done
fi

# прочие верхнеуровневые каталоги (e2e/, integration/, tests/, libs/ …)
for top in */; do
  [ -d "$top" ] || continue
  case "$top" in services/|docs/) continue;; esac
  d=$(nearest_test_dir "$top"); [ -n "$d" ] && dirs+=("$d")
done

json_array ${dirs[@]+"${dirs[@]}"}
