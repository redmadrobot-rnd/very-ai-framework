#!/usr/bin/env bash
#
# Когда:   джобы, которым нужен набор сервисов — deploy-dev.yml (changed),
#          manual-deploy.yml (select), release.yml (list).
# Зачем:   обнаружить сервисы без хардкода имён. Сервис = каталог под services/.
# Вход:    $1 = режим list | changed | select;
#          changed: $2 = base ref, $3 = head ref (дифф между ними);
#          select:  $2 = 'all' либо список сервисов через запятую.
# Алгоритм:
#   list    — все каталоги services/*;
#   changed — сервисы с изменёнными файлами под services/<имя>/ (что пересобирать);
#   select  — 'all' → все; иначе список ∩ реальные каталоги
# Выход:   JSON-массив имён сервисов в stdout.
set -euo pipefail

# имена всех сервисов (каталоги services/*), по одному на строку
all_services() {
  for d in services/*/; do
    [ -d "$d" ] && basename "$d"
  done
  return 0   # пустой глоб → последний [ -d ] = 1; под pipefail это роняло list/select
}

# строки из stdin → компактный JSON-массив (без jq-специфики)
json_array() { jq -R . | jq -sc .; }

# все сервисы
list_services() {
  all_services | json_array
}

# сервисы с изменёнными файлами под services/<имя>/ (что пересобирать)
changed_services() {
  local base="$1" head="$2" changed
  # base может отсутствовать (первый коммит/force-push) — берём предыдущий коммит,
  # а если и его нет — diff против пустого дерева git (все файлы считаются новыми).
  if ! git rev-parse -q --verify "${base}^{commit}" >/dev/null 2>&1; then
    if git rev-parse -q --verify "${head}~1^{commit}" >/dev/null 2>&1; then
      base="${head}~1"
    else
      base=$(git hash-object -t tree /dev/null)   # пустое дерево: 4b825dc6...
    fi
  fi
  changed=$(git diff --name-only "$base" "$head" | awk -F/ '$1=="services" && NF>1 {print $2}' | sort -u)
  comm -12 <(all_services | sort -u) <(printf '%s\n' "$changed" | sed '/^$/d') | json_array
}

# ручной выбор: 'all' → все; иначе введённый список ∩ реальные каталоги
# (отсев несуществующих/мусора — защита от инъекции из workflow_dispatch)
select_services() {
  local input="${1:-all}" requested
  if [ "$input" = "all" ]; then
    all_services | json_array
  else
    requested=$(printf '%s\n' "$input" | tr ',' '\n' | sed 's/[[:space:]]//g; /^$/d' | sort -u)
    comm -12 <(all_services | sort -u) <(printf '%s\n' "$requested") | json_array
  fi
}

case "${1:-list}" in
  list)    list_services ;;
  changed) changed_services "$2" "$3" ;;
  select)  select_services "${2:-all}" ;;
  *) echo "usage: services.sh list|changed|select" >&2; exit 1 ;;
esac
