#!/usr/bin/env bash
#
# Находит тестовые окружения и печатает их JSON-массивом в stdout:
# корневой pyproject.toml + по одному верхнеуровневому на каждый services/<имя>/.
set -euo pipefail

# Окружение = pyproject с [project] или pytest-конфигом ([tool.pytest.*]).
is_test_env() { grep -qE '^\[(project|tool\.pytest)' "$1" 2>/dev/null; }

# Печатает аргументы как компактный JSON-массив строк (без зависимости от jq).
json_array() {
  local out="" item
  for item in "$@"; do
    item=${item//\\/\\\\}; item=${item//\"/\\\"}
    [ -z "$out" ] && out="\"$item\"" || out="$out,\"$item\""
  done
  printf '[%s]' "$out"
}

envs=()

if [ -f pyproject.toml ] && is_test_env pyproject.toml; then
  envs+=(".")
fi

if [ -d services ]; then
  for svc in services/*/; do
    # манифесты сервиса по возрастанию глубины → берём первый подходящий
    while IFS= read -r manifest; do
      if is_test_env "$manifest"; then
        envs+=("$(dirname "$manifest")")
        break
      fi
    done < <(
      find "$svc" -path '*/.venv' -prune -o -path '*/node_modules' -prune -o \
        -name pyproject.toml -print 2>/dev/null \
      | awk -F/ '{print NF, $0}' | sort -k1,1n -k2 | cut -d' ' -f2-
    )
  done
fi

json_array ${envs[@]+"${envs[@]}"}
