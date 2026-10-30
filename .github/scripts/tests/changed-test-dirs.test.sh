#!/usr/bin/env bash
# Тест выбора тест-каталогов (.github/scripts/changed-test-dirs.sh) на временном репо.
#
# Запуск: bash .github/scripts/tests/changed-test-dirs.test.sh
#
# Смысл теста — релиз (tree) видит всё, чем образ отличается от прода: правку прода с
# release/*, которую релиз из main откатывает, и промпты *.txt внутри сервиса; а сбой
# сравнения не превращается в пустую матрицу.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SRC="$ROOT/.github/scripts/changed-test-dirs.sh"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
pass=0; fail=0
ok() { echo "  ok   $1"; pass=$((pass+1)); }
no() { echo "  FAIL $1: ждали «$2», получили «$3»"; fail=$((fail+1)); }
chk() { [ "$2" = "$3" ] && ok "$1" || no "$1" "$3" "$2"; }

ALL='["services/a/workdir","services/b/workdir"]'
cd "$T" && git init -q -b main && git config user.email t@t && git config user.name t
mkdir -p services/a/workdir services/b/workdir
for s in a b; do echo x > "services/$s/workdir/app.py"; echo x > "services/$s/workdir/test_app.py"; done
git add -A && git commit -qm base && base=$(git rev-parse HEAD)
sel() { bash "$SRC" "$ALL" main "$1" "${2:-merge-base}"; }

echo "PR (merge-base)"
git checkout -qb pr && echo y >> services/a/workdir/app.py && git commit -qam a
chk "правка сервиса — его тесты" "$(sel "$base")" '["services/a/workdir"]'
echo y > README.md && git add -A && git commit -qm doc
chk "доки вне сервисов не влияют" "$(sel "$(git rev-parse HEAD~1)")" '[]'
echo y > shared.py && git add -A && git commit -qm shared
chk "общий код — всё" "$(sel "$(git rev-parse HEAD~1)")" "$ALL"

echo "релиз (tree)"
git checkout -q "$base" && git checkout -qb release
echo hotfix >> services/b/workdir/app.py && git commit -qam hotfix && prod=$(git rev-parse HEAD)
git checkout -q -b cand "$base" && echo y >> services/a/workdir/app.py && git commit -qam a2
chk "merge-base не видит откат хотфикса" "$(sel "$prod")" '["services/a/workdir"]'
chk "tree видит и хотфикс прода" "$(sel "$prod" tree)" "$ALL"
git checkout -q -b prompt "$base" && echo y > services/a/workdir/prompt.txt && git add -A && git commit -qm prompt
chk "промпт *.txt в сервисе — его тесты" "$(sel "$base" tree)" '["services/a/workdir"]'
chk "  а в merge-base он фильтруется" "$(sel "$base")" '[]'
chk "неизвестная база — всё" "$(sel 0123456789abcdef0123456789abcdef01234567 tree)" "$ALL"

echo "итог: $pass ok, $fail fail"
[ "$fail" -eq 0 ]
