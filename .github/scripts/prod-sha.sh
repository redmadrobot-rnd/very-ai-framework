#!/usr/bin/env bash
#
# ЧТО ДЕЛАЕТ: перед хотфиксом — от какого коммита резать release/X.Y.x. Тот же ответ, что
#             получает гейт релиза: прочитать .deployed на проде и перевести его в коммит.
# Вход:    $1 = user@host прода (доступы — вне git); $2 = имя проекта на хосте
#          (каталог /srv/deploy/<project>/prod), по умолчанию — имя репо из URL origin.
# Выход:   "<sha> <tag>" в stdout. Дальше: git switch -c release/X.Y.x <tag>
set -euo pipefail

[ $# -ge 1 ] || { echo "usage: prod-sha.sh <user@host> [project]" >&2; exit 1; }
HOST="$1"
origin_name=$(git remote get-url origin | sed -E 's#.*[/:]##; s/\.git$//')
PROJECT="${2:-$origin_name}"
# имя уходит в удалённую команду — тот же фильтр, что у deploy.sh для имени проекта
[[ "$PROJECT" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "invalid project: '$PROJECT'" >&2; exit 1; }
[[ "$HOST" =~ ^[A-Za-z0-9._@-]+$ ]] || { echo "invalid host: '$HOST'" >&2; exit 1; }

dep=$(ssh -o BatchMode=yes -- "$HOST" "cat '/srv/deploy/$PROJECT/prod/.deployed'") \
  || { echo "не прочитать /srv/deploy/$PROJECT/prod/.deployed на $HOST" >&2; exit 1; }
dep="${dep//[$'\r\n']/}"
[ -n "$dep" ] || { echo "на проде пустой .deployed — выкатов через deploy.sh с записью ещё не было" >&2; exit 1; }

git fetch --quiet --tags origin
sha=$(bash "$(dirname "${BASH_SOURCE[0]}")/release-gate.sh" resolve "$dep")
echo "$sha ${dep%% *}"
