#!/usr/bin/env bash
# Тест выбора тест-каталогов (.github/scripts/changed-test-dirs.sh) на временном репо.
#
# Запуск: bash .github/scripts/tests/changed-test-dirs.test.sh
#
# Смысл теста — релиз (tree) видит всё, чем образ отличается от прода: правку прода с
# release/*, которую релиз из main откатывает, промпты *.txt внутри сервиса, перенос файла
# между сервисами; правка сервиса тянет тесты соседа, читающего его исходники; а незнакомая
# база и сбой сравнения не превращаются в пустую матрицу.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SRC="$ROOT/.github/scripts/changed-test-dirs.sh"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
pass=0; fail=0
ok() { echo "  ok   $1"; pass=$((pass+1)); }
no() { echo "  FAIL $1: ждали «$2», получили «$3»"; fail=$((fail+1)); }
chk() { [ "$2" = "$3" ] && ok "$1" || no "$1" "$3" "$2"; }

A='"services/a/workdir"'; B='"services/b/workdir"'; C='"services/c/workdir"'
ALL="[$A,$B,$C]"
cd "$T" && git init -q -b main && git config user.email t@t && git config user.name t
for s in a b c; do
  mkdir -p "services/$s/workdir"; echo x > "services/$s/workdir/app.py"; echo x > "services/$s/workdir/test_app.py"
done
# c читает исходники a по пути — контрактная проверка
printf 'SRC = ROOT / "a" / "workdir" / "app.py"\n' > services/c/workdir/test_contract.py
echo x > services/a/workdir/mod.py
git add -A && git commit -qm base && base=$(git rev-parse HEAD)
sel() { bash "$SRC" "$ALL" main "$1" "${2:-merge-base}"; }

echo "PR (merge-base)"
git checkout -qb pr && echo y >> services/b/workdir/app.py && git commit -qam b
chk "правка сервиса — его тесты" "$(sel "$base")" "[$B]"
echo y >> services/a/workdir/app.py && git commit -qam a
chk "правка сервиса с соседом — и тесты соседа" "$(sel "$(git rev-parse HEAD~1)")" "[$A,$C]"
echo y > README.md && git add -A && git commit -qm doc
chk "доки вне сервисов не влияют" "$(sel "$(git rev-parse HEAD~1)")" '[]'
echo y > shared.py && git add -A && git commit -qm shared
chk "общий код — всё" "$(sel "$(git rev-parse HEAD~1)")" "$ALL"

echo "релиз (tree)"
git checkout -q -b release "$base" && echo hotfix >> services/b/workdir/app.py && git commit -qam hotfix
prod=$(git rev-parse HEAD)
git checkout -q -b cand "$base" && echo y >> services/c/workdir/app.py && git commit -qam c
chk "merge-base не видит откат хотфикса" "$(sel "$prod")" "[$C]"
chk "tree видит и хотфикс прода" "$(sel "$prod" tree)" "[$B,$C]"
git checkout -q -b prompt "$base" && echo y > services/c/workdir/prompt.txt && git add -A && git commit -qm prompt
chk "промпт *.txt в сервисе — его тесты" "$(sel "$base" tree)" "[$C]"
chk "  а в merge-base он фильтруется" "$(sel "$base")" '[]'
git checkout -q -b move "$base" && git mv services/a/workdir/mod.py services/b/workdir/mod.py && git commit -qm move
chk "перенос между сервисами — оба (и сосед)" "$(sel "$base" tree)" "$ALL"
chk "незнакомая база в tree — всё" "$(sel 0123456789abcdef0123456789abcdef01234567 tree)" "$ALL"

echo "итог: $pass ok, $fail fail"
[ "$fail" -eq 0 ]
