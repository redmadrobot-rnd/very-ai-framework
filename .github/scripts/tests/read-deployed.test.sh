#!/usr/bin/env bash
# Тест чтения .deployed с хоста (.github/scripts/read-deployed.sh) с подменённым ssh.
#
# Запуск: bash .github/scripts/tests/read-deployed.test.sh
#
# Смысл теста — три исхода не схлопываются: «файла нет» проходит как пусто, а «не смог
# прочитать» (нет каталога, нет прав, хост недоступен) — отказ. Пусто для гейта значит
# «первый релиз, монотонность не проверяем», и туда нельзя пускать ошибку чтения.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SRC="$ROOT/.github/scripts/read-deployed.sh"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
pass=0; fail=0
ok() { echo "  ok   $1"; pass=$((pass+1)); }
no() { echo "  FAIL $1: ждали «$2», получили «$3»"; fail=$((fail+1)); }
chk() { [ "$2" = "$3" ] && ok "$1" || no "$1" "$3" "$2"; }
has() { case "$2" in *"$3"*) ok "$1" ;; *) no "$1" "подстроку «$3»" "$2" ;; esac; }

# Фальшивый ssh: исполняет присланную команду локально, подменив /srv/deploy на $FX_ROOT.
# FX_DOWN=1 — «хост недоступен» (ssh rc=255).
mkdir -p "$T/bin"
cat > "$T/bin/ssh" <<'SHIM'
#!/usr/bin/env bash
[ "${FX_DOWN:-}" = 1 ] && { echo "ssh: connect to host x port 22: Connection refused" >&2; exit 255; }
cmd="${@: -1}"
bash -c "${cmd//\/srv\/deploy/$FX_ROOT}"
SHIM
chmod +x "$T/bin/ssh"

export SSH="$T/bin/ssh" SSH_HOST=h SSH_USER=u SSH_KEY=k PROJECT=proj ENVIRONMENT=prod FX_ROOT="$T/srv"
run() { out=$(bash "$SRC" 2>"$T/err"); rc=$?; err=$(cat "$T/err"); }

echo "исходы"
run; chk "нет каталога деплоя — отказ" "$rc" 1; has "  текст" "$err" "fail closed"

mkdir -p "$T/srv/proj/prod"
run; chk "каталог есть, файла нет — пусто, rc 0" "$rc" 0; chk "  stdout пуст" "$out" ""; has "  notice" "$err" "отсутствует"

printf 'v1.9.3\r\n' > "$T/srv/proj/prod/.deployed"
run; chk "файл с тегом — содержимое без \\r\\n" "$out" "v1.9.3"

printf 'v1.9.3 %s\n' "$(printf 'a%.0s' {1..40})" > "$T/srv/proj/prod/.deployed"
run; chk "тег + sha — целиком" "$out" "v1.9.3 $(printf 'a%.0s' {1..40})"

printf 'v1; rm -rf /\n' > "$T/srv/proj/prod/.deployed"
run; chk "мусор в файле — отказ" "$rc" 1; has "  текст" "$err" "странное содержимое"

printf 'v1.9.3\n' > "$T/srv/proj/prod/.deployed"; chmod 000 "$T/srv/proj/prod/.deployed"
# где chmod 000 не запрещает чтение (root в CI-контейнере, NTFS под Git Bash), исход «прочитали» честен
if cat "$T/srv/proj/prod/.deployed" >/dev/null 2>&1; then
  ok "нет прав — пропуск: chmod 000 здесь чтению не мешает"
else
  run; chk "нет прав — отказ, не пусто" "$rc" 1
fi
chmod 644 "$T/srv/proj/prod/.deployed"

FX_DOWN=1 run; chk "хост недоступен — отказ" "$rc" 1; has "  текст" "$err" "fail closed"

echo
echo "read-deployed: $pass ok, $fail fail"
[ "$fail" = 0 ]
