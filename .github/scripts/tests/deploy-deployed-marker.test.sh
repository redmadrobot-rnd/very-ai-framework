#!/usr/bin/env bash
# Тест записи .deployed в deploy.sh — правды о том, что стоит на окружении.
#
# Запуск: bash .github/scripts/tests/deploy-deployed-marker.test.sh
#
# Правила: пишется ТОЛЬКО после выката всего стека; только для релизной координаты
# (vX… или manual-<hex>); со sha, если его дали; точечный выкат прошлое значение не трогает.
# Прод целиком без координаты или без sha — отказ до любого касания стека.
#
# Шим docker — свой и полный: всё только пишется в журнал, настоящий docker не нужен.
set -uo pipefail

BASH_BIN="${BASH:-$(command -v bash)}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SRC="$ROOT/.github/scripts/deploy.sh"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
pass=0; fail=0
ok() { echo "  ok   $1"; pass=$((pass+1)); }
no() { echo "  FAIL $1: ждали «$2», получили «$3»"; fail=$((fail+1)); }
chk() { [ "$2" = "$3" ] && ok "$1" || no "$1" "$3" "$2"; }
has() { case "$2" in *"$3"*) ok "$1" ;; *) no "$1" "подстроку «$3»" "$(printf '%s' "$2" | tail -3)" ;; esac; }

mkdir -p "$T/bin"
cat > "$T/bin/docker" <<'SHIM'
#!/usr/bin/env bash
case "$1" in
  login) echo login >> "$FX_LOG" ;;
  compose)
    for a in "${@:2}"; do case "$a" in pull|up|ps) echo "compose $a" >> "$FX_LOG"; break ;; esac; done ;;
esac
exit 0
SHIM
chmod +x "$T/bin/docker"

mkdir -p "$T/dir"
printf 'services:\n  a: {image: "x/a:latest"}\n' > "$T/dir/docker-compose.yml"

sed -e 's#^DIR="/srv/deploy/.*#DIR="${FX_DIR}"#' \
    -e 's#^export IMAGE_PREFIX=.*#export IMAGE_PREFIX="${FX_PREFIX}"#' "$SRC" > "$T/deploy.sh"
chk "правка копии — ровно 2 строки" "$(diff "$SRC" "$T/deploy.sh" | grep -c '^> ')" "2"

SHA=0123456789abcdef0123456789abcdef01234567
run() {  # run <журнал> <env=...>... -- <аргументы deploy.sh>
  local log="$1"; shift; local envs=()
  while [ "$1" != "--" ]; do envs+=("$1"); shift; done; shift
  : > "$T/$log"
  env -i HOME="$HOME" PATH="$T/bin:$PATH" FX_LOG="$T/$log" FX_DIR="$T/dir" FX_PREFIX="registry.invalid/fake" \
      GHCR_USER=u GHCR_TOKEN=t "${envs[@]}" "$BASH_BIN" "$T/deploy.sh" "$@" 2>&1
}
deployed() { cat "$T/dir/.deployed" 2>/dev/null || echo "<нет>"; }
seed() { printf '%s\n' "$1" > "$T/dir/.deployed"; }
mutations() { grep -c '^compose \(pull\|up\)' "$T/$1"; }

echo "dev"
rm -f "$T/dir/.deployed"
out=$(run j1 DEPLOY_SHA=$SHA -- dev fake/repo v1 all); chk "весь стек, v-тег со sha — выкат прошёл" "$?" 0
chk "  .deployed = тег + sha" "$(deployed)" "v1 $SHA"
out=$(run j2 -- dev fake/repo v2 all); chk "весь стек без DEPLOY_SHA (dev) — можно" "$?" 0
chk "  .deployed = только тег" "$(deployed)" "v2"

seed "v2"
out=$(run j3 DEPLOY_SHA=$SHA -- dev fake/repo v3 a); chk "точечный выкат — прошёл" "$?" 0
chk "  .deployed не тронут" "$(deployed)" "v2"; has "  и сказано почему" "$out" "не всего стека"

out=$(run j4 -- dev fake/repo latest all); chk "latest на dev — выкат прошёл" "$?" 0
chk "  .deployed не тронут" "$(deployed)" "v2"; has "  и сказано почему" "$out" "не релизная координата"

echo "prod — весь стек только на релизную координату со sha"
seed "v2 $SHA"
out=$(run j6 -- prod fake/repo latest all); chk "latest целиком — отказ" "$?" 1
has "  назван" "$out" "vX.Y.Z или manual-<sha>"; chk "  стек не тронут" "$(mutations j6)" 0
chk "  и login не было" "$(grep -c '^login' "$T/j6")" 0

out=$(run j7 -- prod fake/repo v5 all); chk "v-тег без DEPLOY_SHA целиком — отказ" "$?" 1
has "  подсказано, как дать sha" "$out" "DEPLOY_SHA"; chk "  стек не тронут" "$(mutations j7)" 0
chk "  .deployed прежний" "$(deployed)" "v2 $SHA"

out=$(run j7s DEPLOY_SHA=0123abc -- prod fake/repo v5 all); chk "v-тег с коротким sha целиком — отказ" "$?" 1
has "  назван формат" "$out" "40 hex"; chk "  стек не тронут" "$(mutations j7s)" 0

out=$(run j8 DEPLOY_SHA=$SHA -- prod fake/repo v5 all); chk "v-тег со sha целиком — выкат" "$?" 0
chk "  .deployed = v5 + sha" "$(deployed)" "v5 $SHA"

out=$(run j9 DEPLOY_SHA=$SHA -- prod fake/repo manual-abc1234 all); chk "manual-<hex> со sha целиком — выкат" "$?" 0
chk "  .deployed = manual + sha" "$(deployed)" "manual-abc1234 $SHA"

out=$(run j10 -- prod fake/repo latest a); chk "точечный latest на прод — не ограничен" "$?" 0
chk "  .deployed не тронут" "$(deployed)" "manual-abc1234 $SHA"

echo "точечный запрос при сменившемся compose — это выкат всего стека"
printf 'services:
  a: {image: "x/a:latest"}
  b: {image: "x/b:latest"}
' > "$T/dir/docker-compose.yml"
out=$(run j11 -- prod fake/repo latest a); chk "prod: latest a при новом compose — отказ" "$?" 1
has "  назван" "$out" "vX.Y.Z или manual-<sha>"; chk "  стек не тронут" "$(mutations j11)" 0
out=$(run j12 DEPLOY_SHA=$SHA -- prod fake/repo v6 a); chk "prod: v6 a со sha при новом compose — выкат" "$?" 0
chk "  .deployed = v6 + sha" "$(deployed)" "v6 $SHA"
out=$(run j13 DEPLOY_SHA=$SHA -- prod fake/repo v7 a); chk "следом точечный при том же compose — прошёл" "$?" 0
chk "  .deployed не тронут" "$(deployed)" "v6 $SHA"

echo
echo "deploy-deployed-marker: $pass ok, $fail fail"
[ "$fail" = 0 ]
