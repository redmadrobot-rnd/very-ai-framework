#!/usr/bin/env bash
# Тест гейта релиза (.github/scripts/release-gate.sh) на настоящем git-репо с настоящим
# origin (bare).
#
# Запуск: bash .github/scripts/tests/release-gate.test.sh
#
# История, которую строим (squash-only, как в репо):
#
#   main:     Mp(v2.1.0) — M0(v2.2.0) — M1 — M2 — M3 — S(squash release/2.2.x) — M5
#   release:                 └─ H1(v2.2.1) — H2(v2.2.2)
#   feature:                                  └─ F  (не на релизной линии)
#
# Проверяем каждую строку правила из шапки скрипта и каждый отказ, который видит оператор.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SRC="$ROOT/.github/scripts/release-gate.sh"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
pass=0; fail=0

ok() { echo "  ok   $1"; pass=$((pass+1)); }
no() { echo "  FAIL $1: ждали «$2», получили «$3»"; fail=$((fail+1)); }
chk() { [ "$2" = "$3" ] && ok "$1" || no "$1" "$3" "$2"; }
has() { case "$2" in *"$3"*) ok "$1" ;; *) no "$1" "подстроку «$3»" "$(printf '%s' "$2" | tail -3)" ;; esac; }

# ── репо ────────────────────────────────────────────────────────────────────────────────
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
git init -q --bare "$T/origin.git"
git init -q -b main "$T/work"
# без set -e: не вышло зайти во временный репо — стоп, иначе коммиты, теги и push пойдут в текущий
cd "$T/work" || { echo "FAIL: нет $T/work — тест не гоняем в чужом репо"; exit 1; }
git remote add origin "$T/origin.git"
mkdir -p services/a services/b
# mkdir: switch на ветку без services/b/ уносит пустой каталог, и запись в него молча падала бы
c() { mkdir -p "$(dirname "$2")"; echo "$1" >> "$2"; git add -A; git commit -q -m "$1"; git rev-parse HEAD; }

Mp=$(c Mp services/a/f); git tag v2.1.0
M0=$(c M0 services/a/f); git tag v2.2.0
M1=$(c M1 services/b/f); M2=$(c M2 services/b/f); M3=$(c M3 services/a/f)

git switch -q -c release/2.2.x v2.2.0
H1=$(c H1 services/a/hot); git tag v2.2.1
H2=$(c H2 services/b/hot); git tag v2.2.2
git switch -q -c feature/x
F=$(c F services/a/feat)

git switch -q main
# squash форвард-порта: содержимое H1+H2 одним коммитом, предком H2 не является
git checkout -q release/2.2.x -- services/a/hot services/b/hot
git commit -q -m "S (squash release/2.2.x)"; S=$(git rev-parse HEAD)
M5=$(c M5 services/b/f)

# вторая линия хотфиксов от другой точки — для проверки «параллельных» патчей
git switch -q -c release/2.2.y v2.2.0
Ha=$(c Ha services/a/other); git tag v2.2.9
git switch -q main

# --all и --tags git вместе не принимает
git push -q origin --all && git push -q origin --tags

export REMOTE=origin MAIN=main
gate() { out=$(bash "$SRC" check "$@" 2>&1); rc=$?; }

echo "resolve"
chk "vX → коммит тега"           "$(bash "$SRC" resolve v2.2.1 2>&1)" "$H1"
chk "manual-<sha> → коммит"       "$(bash "$SRC" resolve "manual-$(git rev-parse --short "$M1")" 2>&1)" "$M1"
out=$(bash "$SRC" resolve v9.9.9 2>&1); chk "неизвестный тег — отказ" "$?" 1; has "  текст" "$out" "не найден"
out=$(bash "$SRC" resolve garbage 2>&1); chk "мусор — отказ" "$?" 1; has "  текст" "$out" "непонятное содержимое"
out=$(bash "$SRC" resolve manual-main 2>&1); chk "manual-<не hex> — отказ, ветку не резолвим" "$?" 1; has "  текст" "$out" "hex-sha"
out=$(bash "$SRC" resolve latest 2>&1); chk "latest — отказ" "$?" 1

echo ".deployed с sha"
chk "tag+sha → sha"                  "$(bash "$SRC" resolve "v2.2.1 $H1" 2>&1)" "$H1"
out=$(bash "$SRC" resolve "v2.2.1 $H2" 2>&1); chk "тег сдвинут относительно sha — отказ" "$?" 1; has "  текст" "$out" "сдвинут"
out=$(bash "$SRC" resolve "v9.9.9 $H1" 2>&1); chk "тег удалён, sha есть — sha побеждает" "$?" 0
out=$(bash "$SRC" resolve "v2.2.1 0000000000000000000000000000000000000000" 2>&1); chk "sha нет в репо — отказ" "$?" 1

echo "1. линия тега"
gate "$M0" "";   chk "тег на main, .deployed пуст — можно (первый релиз)" "$rc" 0; has "  notice" "$out" "первый релиз"
gate "$H1" "";   chk "тег на release/*, .deployed пуст — можно" "$rc" 0
gate "$F"  "";   chk "тег на feature/* — отказ" "$rc" 1; has "  текст" "$out" "не на first-parent"
GITHUB_OUTPUT="$T/gh_out" gate "$H1" ""; chk "линия уходит в GITHUB_OUTPUT (пину)" "$(cat "$T/gh_out")" "line=release/2.2.x"
GITHUB_OUTPUT="$T/gh_out2" gate "$M0" ""; chk "  и для main" "$(cat "$T/gh_out2")" "line=main"

echo "3. D предок T"
gate "$M0" v2.1.0; chk "обычный релиз: v2.1.0 → M0" "$rc" 0
gate "$H1" v2.2.0; chk "хотфикс: v2.2.0 → H1" "$rc" 0
gate "$H2" v2.2.1; chk "патч поверх патча: v2.2.1 → H2" "$rc" 0
gate "$H2" "v2.2.1 $H1"; chk "то же с парой tag+sha" "$rc" 0
gate "$Mp" v2.2.0; chk "тег старше прода — отказ" "$rc" 1; has "  текст" "$out" "старше текущего прода"

echo "4. релиз с main после хотфиксов (прод на v2.2.2 = H2)"
gate "$M5" v2.2.2; chk "тег содержит точку отрастания релиза — можно" "$rc" 0
has "  предупреждение: форвард-порт на совести разработчиков" "$out" "гейт не проверяет"
gate "$M3" v2.2.2; chk "тег до squash — тоже можно (форвард-порт не проверяем)" "$rc" 0
gate "$Mp" v2.2.2; chk "тег до точки отрастания — отказ" "$rc" 1

echo "параллельные хотфиксы"
gate "$Ha" v2.2.2; chk "второй хотфикс от той же точки, не поверх первого — отказ" "$rc" 1; has "  текст" "$out" "не потомок текущего прода"

echo "откат"
gate "$M5" "v2.2.2 $H2";  chk "после отката на патч ветки следующий релиз с main проходит" "$rc" 0
gate "$M5" "v2.2.0 $M0";  chk "после отката на v2.2.0 — main-тег проходит как обычно" "$rc" 0

echo "прочее"
gate "$M5" v9.9.9; chk "неизвестный тег в .deployed — отказ" "$rc" 1

echo
echo "release-gate: $pass ok, $fail fail"
[ "$fail" = 0 ]
