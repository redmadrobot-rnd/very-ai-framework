#!/usr/bin/env bash
#
# Когда:   джоб changed в deploy-dev.yml (и manual.yml) — определить, что собирать.
# Зачем:   обнаружить сервисы без хардкода имён. Сервис = каталог под services/.
# Вход:    $1 = режим list | changed | select;
#          changed: $2 = base ref, $3 = head ref (дифф между ними);
#          select:  $2 = 'all' либо список сервисов через запятую.
# Алгоритм:
#   list    — все каталоги services/*;
#   changed — сервисы, чьи файлы изменились в base..head; правка docker-compose.yml
#             или deploy.sh → пересобрать ВСЕ (влияет на весь стек);
#   select  — 'all' → все; иначе список ∩ реальные каталоги (отсев мусора/инъекции).
# Выход:   JSON-массив имён сервисов в stdout.
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
  select)
    # ручной выбор: 'all' -> все; иначе список через запятую. Несуществующие имена
    # отсекаются пересечением с реальными каталогами (как в changed) — заодно защита
    # от мусора/инъекции во входе workflow_dispatch.
    input="${2:-all}"
    if [ "$input" = "all" ]; then
      all_services | jq -R . | jq -sc .
    else
      requested=$(printf '%s\n' "$input" | tr ',' '\n' | sed 's/[[:space:]]//g; /^$/d' | sort -u)
      comm -12 <(all_services | sort -u) <(printf '%s\n' "$requested") | jq -R . | jq -sc .
    fi
    ;;
  *)
    echo "usage: services.sh list|changed|select" >&2
    exit 1
    ;;
esac
