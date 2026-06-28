#!/usr/bin/env bash
# Обнаружение сервисов проекта — без хардкода имён. Сервис = каталог под services/.
#
#   services.sh list                   -> JSON-массив всех сервисов
#   services.sh changed <base> <head>  -> JSON-массив сервисов, изменённых между refs
set -euo pipefail

mode="${1:-list}"

all_services() {
  for d in services/*/; do
    [ -d "$d" ] && basename "$d"
  done
}

case "$mode" in
  list)
    all_services | jq -R . | jq -sc .
    ;;
  changed)
    base="$2"; head="$3"
    # на первом коммите/force-push base может отсутствовать — берём предыдущий коммит,
    # а если и его нет (самый первый коммит репо) — diff против пустого дерева git,
    # тогда все файлы считаются добавленными и собираются все сервисы.
    if ! git rev-parse -q --verify "${base}^{commit}" >/dev/null 2>&1; then
      if git rev-parse -q --verify "${head}~1^{commit}" >/dev/null 2>&1; then
        base="${head}~1"
      else
        base=$(git hash-object -t tree /dev/null)   # пустое дерево: 4b825dc6...
      fi
    fi
    files=$(git diff --name-only "$base" "$head")
    if printf '%s\n' "$files" | grep -qE '^(docker-compose\.yml|\.github/scripts/deploy\.sh)$'; then
      all_services | jq -R . | jq -sc .
      exit 0
    fi
    changed=$(printf '%s\n' "$files" | awk -F/ '$1=="services" && NF>1 {print $2}' | sort -u)
    comm -12 <(all_services | sort -u) <(printf '%s\n' "$changed" | sed '/^$/d') | jq -R . | jq -sc .
    ;;
  *)
    echo "usage: services.sh list|changed" >&2
    exit 1
    ;;
esac
