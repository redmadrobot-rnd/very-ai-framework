#!/usr/bin/env bash
#
# Находит тестовые окружения и печатает их компактным JSON-массивом в stdout.
#
# Окружение = каталог с pyproject.toml, содержащим секцию [project] (uv-проект).
# Правило обнаружения:
#   - корневой pyproject.toml (плоский репозиторий / общие и e2e-тесты в корне);
#   - по каждому services/<имя>/ — ОДИН верхнеуровневый pyproject.toml
#     (путь внутри сервиса не важен: workdir/, backend/, корень — любой).
# Имена сервисов нигде не зашиты; новый сервис подхватывается автоматически.
#
# Запуск локально для проверки:  bash .github/scripts/discover-envs.sh
set -euo pipefail

# Тестовое окружение: установимый проект ([project]) ЛИБО каталог с pytest-конфигом
# ([tool.pytest.*]). Второе — чтобы тесты не пропадали молча в tooling-only репозиториях
# (pyproject только с настройками ruff/pytest, без [project]).
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
    # манифесты сервиса по возрастанию глубины → берём первый с [project]
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
