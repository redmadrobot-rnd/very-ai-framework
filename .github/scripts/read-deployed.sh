#!/usr/bin/env bash
#
# ЧТО ДЕЛАЕТ: читает с хоста, что на нём стоит, — файл .deployed, который deploy.sh пишет
#             после успешного выката всего стека. Нужен гейту релиза (release.yml: prepare и
#             перепроверка в deploy-prod).
# Вход:    env SSH_HOST, SSH_USER, SSH_KEY (текст ключа), PROJECT, ENVIRONMENT;
#          SSH (бинарь — подменяется в тестах).
# Алгоритм: три исхода различаются намеренно —
#            файла нет, каталог есть → пусто (ещё ни разу не писали);
#            файл есть               → его содержимое;
#            всё остальное (нет каталога, нет прав, хост недоступен) → rc≠0.
#          Схлопнуть ошибки чтения в «пусто» нельзя: пусто = «первый релиз», гейт его пропускает.
# Выход:   содержимое .deployed в stdout (одна строка, без \r\n), валидированное.
set -euo pipefail

SSH="${SSH:-ssh}"
: "${SSH_HOST:?}" "${SSH_USER:?}" "${SSH_KEY:?}" "${PROJECT:?}" "${ENVIRONMENT:?}"

f="/srv/deploy/$PROJECT/$ENVIRONMENT/.deployed"
# На хосте: файл → cat; нет файла в доступном каталоге → маркер; иначе — рассказать, что увидели, rc 3.
remote="if [ -f '$f' ]; then cat '$f';
        elif [ -d '${f%/*}' ] && [ -x '${f%/*}' ] && [ ! -e '$f' ]; then echo __absent__;
        else echo \"__unreadable__ \$(ls -ld '$f' '${f%/*}' 2>&1 | tr '\n' ' ')\" >&2; exit 3; fi"

key=$(mktemp); trap 'rm -f "$key"' EXIT
printf '%s\n' "$SSH_KEY" > "$key"; chmod 600 "$key"

out=$("$SSH" -i "$key" -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 \
        "$SSH_USER@$SSH_HOST" "$remote") \
  || { echo "::error::не удалось прочитать $f на $SSH_HOST (rc=$?) — гейт не скипаем, fail closed. Новый хост без выкатов? Создай каталог руками: mkdir -p ${f%/*}" >&2; exit 1; }

out="${out//[$'\r\n']/}"
if [ "$out" = "__absent__" ]; then
  echo "::notice::$f отсутствует — выкатов с записью ещё не было" >&2
  echo ""
  exit 0
fi
[[ "$out" =~ ^[A-Za-z0-9_.-]{1,128}( [0-9a-f]{40})?$ ]] \
  || { echo "::error::странное содержимое $f: '$out'" >&2; exit 1; }
echo "$out"
