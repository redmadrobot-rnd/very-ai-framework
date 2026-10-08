#!/usr/bin/env bash
#
# ЧТО ДЕЛАЕТ: отвечает, можно ли выкатывать релизный тег поверх того, что СЕЙЧАС стоит на
#             проде. Правда о проде — файл .deployed на хосте (пишет deploy.sh после
#             успешного выката всего стека), а не git-метка: метка врёт после упавшего выката
#             и после отката через manual-deploy, файл — нет.
# Вход:    $1 = режим resolve | check
#          resolve <deployed>            → sha коммита за содержимым .deployed
#          check   <tag_sha> <deployed>  → 0 = выкатывать можно, 1 = нельзя (причина в stderr)
#          <deployed> — содержимое .deployed: "vX.Y.Z" | "vX.Y.Z <sha>" | "manual-<sha> <sha>" | пусто
#          env: REMOTE (origin), MAIN (main); GITHUB_OUTPUT — если задан, туда уходит line=<линия>.
# Алгоритм (T — тег, D — коммит из .deployed):
#   1. T на first-parent-истории main или одной из веток release/* — иначе отказ;
#   2. .deployed пуст → первый релиз, монотонность не проверяем;
#   3. D предок T → можно (обычный релиз; патч поверх патча на release/*);
#   4. иначе только для T на main: T обязан содержать merge-base(main, D) — релиз с main
#      после хотфиксов. Что сами хотфиксы уже в main, гейт НЕ проверяет — это на авторе
#      хотфикса. T на release/*, не потомок D — отказ: патчи одной линии ложатся друг на друга.
# Выход:   код возврата; любая ошибка чтения git — отказ, не пропуск.
set -euo pipefail

REMOTE="${REMOTE:-origin}"
MAIN="${MAIN:-main}"

die() { echo "::error::$*" >&2; exit 1; }

# .deployed = "<tag>" или "<tag> <sha>". Со sha — он и есть ответ, тег лишь сверяется:
# сдвинутый тег иначе подменил бы точку отсчёта молча. Без sha — резолвим тег.
resolve() {
  local dep="$1" tag sha by_tag
  tag="${dep%% *}"; sha=""; [ "$tag" != "$dep" ] && sha="${dep#* }"
  case "$tag" in
    v*)
      by_tag=$(git rev-parse -q --verify "refs/tags/$tag^{commit}") || by_tag="";;
    manual-*)
      # только hex: `manual-main` резолвился бы в ветку, и гейт считал бы прод от чужого коммита
      [[ "${tag#manual-}" =~ ^[0-9a-f]{7,40}$ ]] \
        || die "непонятное содержимое .deployed: '$dep' (после manual- ждём hex-sha)"
      by_tag=$(git rev-parse -q --verify "${tag#manual-}^{commit}") || by_tag="";;
    *) die "непонятное содержимое .deployed: '$dep' (ждём vX.Y.Z или manual-<sha>)";;
  esac
  if [ -n "$sha" ]; then
    # коммит ручного выката с удалённой после squash ветки ни одним ref не достижим —
    # GitHub отдаёт его по sha напрямую
    git rev-parse -q --verify "$sha^{commit}" >/dev/null \
      || git fetch --quiet "$REMOTE" "$sha" 2>/dev/null \
      || die "коммит $sha из .deployed ('$dep') не найден в репо — история переписана?"
    [ -z "$by_tag" ] || [ "$by_tag" = "$sha" ] \
      || die "тег '$tag' сдвинут: в .deployed он указывал на ${sha:0:12}, в репо теперь ${by_tag:0:12}. Теги релизов неприкосновенны — верни тег на место."
    echo "$sha"; return 0
  fi
  [ -n "$by_tag" ] || die "'$tag' из .deployed не найден в репо — тег удалён или не дотянут (fetch --tags)"
  echo "$by_tag"
}

# grep без -q: иначе rev-list получает SIGPIPE, и под pipefail это выглядит как «не найдено»
on_first_parent() { git rev-list --first-parent "$1" | grep -x "$2" >/dev/null; }

check() {
  local t="$1" dep="$2" d base line="" b

  git fetch --quiet "$REMOTE" \
    "+refs/heads/$MAIN:refs/remotes/$REMOTE/$MAIN" \
    "+refs/heads/release/*:refs/remotes/$REMOTE/release/*" \
    "+refs/tags/*:refs/tags/*" \
    || die "не удалось получить ветки/теги из $REMOTE — гейт не скипаем, fail closed"

  if on_first_parent "refs/remotes/$REMOTE/$MAIN" "$t"; then
    line="$MAIN"
  else
    for b in $(git for-each-ref --format='%(refname)' "refs/remotes/$REMOTE/release/"); do
      if on_first_parent "$b" "$t"; then line="${b#refs/remotes/"$REMOTE"/}"; break; fi
    done
  fi
  [ -n "$line" ] || die "тег ($t) не на first-parent-истории $MAIN и ни одной ветки release/*. Релизный тег ставится на коммит $MAIN или ветки release/X.Y.x. Хотфикс: ветка обязана быть в $REMOTE — git push $REMOTE release/X.Y.x, затем re-run."
  echo "тег на линии $line"
  # линия нужна пину: недостающий образ на main — недобитый dev-build (отказ), на release/* — норма (сборка)
  [ -z "${GITHUB_OUTPUT:-}" ] || echo "line=$line" >> "$GITHUB_OUTPUT"

  if [ -z "$dep" ]; then
    echo "::notice::.deployed на проде пуст — первый релиз, монотонность не проверяем"
    return 0
  fi
  d=$(resolve "$dep")

  if git merge-base --is-ancestor "$d" "$t"; then
    echo "прод стоит на $dep (${d:0:12}), тег — его потомок"
    return 0
  fi

  # Два хотфикса от одной точки на разных ветках — не «параллельные», а откат одного другим.
  [ "$line" = "$MAIN" ] \
    || die "тег ($t) на $line не потомок текущего прода ($dep, ${d:0:12}). Патчи одной линии ложатся один поверх другого — перебазируй ветку на ${d:0:12} (или продолжай ту ветку, с которой уехал прод)."

  base=$(git merge-base "refs/remotes/$REMOTE/$MAIN" "$d") \
    || die "нет общей истории у $MAIN и прода ($dep) — репо в неожиданном состоянии"
  git merge-base --is-ancestor "$base" "$t" \
    || die "тег ($t) старше текущего прода: прод стоит на $dep (${d:0:12}). Релиз двигает прод только вперёд; откат — manual-deploy предыдущего :vX (build=off)."

  echo "::notice::прод стоит на патче ветки релиза ($dep, ${d:0:12}), тег — с $MAIN после точки ${base:0:12}. Что хотфиксы этой ветки уже в $MAIN, гейт не проверяет."
  return 0
}

case "${1:-}" in
  resolve) [ $# -eq 2 ] || die "usage: release-gate.sh resolve <deployed>"; resolve "$2" ;;
  check)   [ $# -eq 3 ] || die "usage: release-gate.sh check <tag_sha> <deployed>"; check "$2" "$3" ;;
  *) die "usage: release-gate.sh resolve <deployed> | check <tag_sha> <deployed>" ;;
esac
