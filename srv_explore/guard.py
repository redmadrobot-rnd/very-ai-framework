#!/usr/bin/env python3
"""srv-explore — PreToolUse-гард.

Читает PreToolUse-JSON со stdin, сверяет Bash-команду с политикой «только чтение»:
    allow → JSON permissionDecision=allow, exit 0;
    deny  → причина в stderr + permissionDecision=deny, exit 2 (hard block).
Fail-closed: любая неоднозначность → deny.

Политика собрана из ПРОФИЛЕЙ — модулей profiles/*.py. Каждый профиль:
    ID: str, COMMANDS: list[str], DESC: str
    def check(argv, g) -> (ok, reason)   # g — тулкит хелперов (ниже)
Профиль владеет своими COMMANDS. Профиль выключен/отсутствует → его команды deny.
Команда без владельца → падает в fallback-профиль local.py (базовая read-оболочка).
Метасимволы/пайпы/чтение спецфайлов — общие инварианты ядра, до профилей.

Формат и пример профиля — profiles/FORMAT.md, profiles/profile.py.example.
"""

from __future__ import annotations

import importlib.util
import ipaddress
import json
import os
import re
import shlex
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # Windows-консоль иначе бьёт кириллицу
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
PROFILES = Path(os.environ.get("SRV_EXPLORE_PROFILES_DIR", str(HERE / "profiles")))
STATE = os.environ.get("SRV_EXPLORE_PROFILE_STATE", "/var/lib/srv-explore/profiles.json")

# Метасимволы записи/сайд-эффекта/подстановки/цепочки. Пайп (|) — отдельно (read-пайплайны).
DANGEROUS = ["`", "$(", ">", "<", ";", "&", "\n", "\r"]
# Спецфайлы: сырые устройства/бесконечные источники вешают/эксфильтрируют.
SAFE_DEV = {"/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/tty"}

DOCKER_VALUE_FLAGS = {
    "-H", "--host", "--context", "--config", "-l", "--log-level",
    "--tlscacert", "--tlscert", "--tlskey",
}
COMPOSE_VALUE_FLAGS = {
    "-f", "--file", "-p", "--project-name", "--project-directory",
    "--env-file", "--profile", "--ansi", "--progress",
}
EXEC_VALUE_FLAGS = {"-u", "--user", "-e", "--env", "-w", "--workdir", "--env-file"}
EXEC_DENY_FLAGS = {"--privileged", "-d", "--detach"}
EXEC_OK_BOOL_FLAGS = {"-i", "-t", "-it", "-ti", "--interactive", "--tty"}
FOLLOW_FLAGS = {"-f", "--follow"}

# curl — allowlist безопасного GET (deny-unknown, fail-closed).
CURL_SHORT_BOOL = set("sSiILkfgv46#")
CURL_SHORT_VALUE = {"m", "A", "e", "H"}
CURL_LONG_BOOL = {
    "--silent", "--show-error", "--include", "--head", "--location", "--insecure",
    "--compressed", "--fail", "--globoff", "--verbose", "--ipv4", "--ipv6",
    "--progress-bar", "--no-progress-meter",
}
CURL_LONG_VALUE = {
    "--max-time", "--connect-timeout", "--user-agent", "--referer", "--resolve",
    "--retry", "--header", "--max-redirs", "--limit-rate",
}


# --- примитивы разбора -------------------------------------------------------

def has_follow(argv: list[str]) -> bool:
    for a in argv:
        if a in FOLLOW_FLAGS or a.startswith("--follow="):
            return True
        if a.startswith("-") and not a.startswith("--") and "f" in a[1:]:
            return True
    return False


def tail_follows(argv: list[str]) -> bool:
    for a in argv:
        if a in ("-f", "-F", "--follow") or a.startswith("--follow="):
            return True
        if a.startswith("-") and not a.startswith("--") and "f" in a[1:]:
            return True
    return False


def forbidden_path(tok: str) -> bool:
    p = tok.split("=", 1)[-1].strip("\"'") if "=" in tok else tok
    if p in SAFE_DEV or p.startswith("/dev/fd/"):
        return False
    return p.startswith("/dev/") or p.startswith("/proc/kcore")


def subcommand(args: list[str], value_flags: set[str]) -> tuple[str | None, list[str]]:
    """Первый позиционный токен и хвост, пропуская флаги и их значения."""
    i = 0
    while i < len(args):
        a = args[i]
        if a in value_flags:
            i += 2
            continue
        if a.startswith("--") and "=" in a:
            i += 1
            continue
        if a.startswith("-"):
            i += 1
            continue
        return a, args[i + 1:]
    return None, []


# --- SQL / Mongo парсеры (ядро) ----------------------------------------------

def sql_read_guard(sql: str, allow_prefixes, forbid) -> tuple[bool, str]:
    body = re.sub(r"--[^\n]*", " ", sql)
    body = re.sub(r"/\*.*?\*/", " ", body, flags=re.S).strip().strip(";").strip()
    if not body:
        return False, "пустой SQL"
    if ";" in body:
        return False, "несколько стейтментов запрещено (одна инструкция на запрос)"
    first = body.split(None, 1)[0].lower()
    allow = [p.lower() for p in allow_prefixes]
    if first not in allow:
        return False, f"стейтмент '{first}' не read-only (разрешено: {', '.join(allow)})"
    if re.search(r"\bexplain\b", body, re.I) and re.search(r"\banalyze\b", body, re.I):
        return False, "EXPLAIN ANALYZE выполняет запрос — запрещено"
    for kw in forbid:
        if re.search(rf"\b{re.escape(kw)}\b", body, re.I):
            return False, f"запрещённое ключевое слово/функция: {kw}"
    return True, "sql read-only"


def _extract_args(argv, take_flags, file_flags, label) -> tuple[list[str] | None, str]:
    """Собрать значения take_flags (-c/--eval); file_flags и интерактив — deny."""
    vals, i = [], 1
    while i < len(argv):
        a = argv[i]
        if a in file_flags or any(
            a.startswith(f + "=") or (a.startswith(f) and len(a) > len(f) and not f.startswith("--"))
            for f in file_flags
        ):
            return None, f"{label}: скрипт/файл-инструкция не проверяется гардом — запрещено"
        if a in take_flags:
            if i + 1 >= len(argv):
                return None, f"{label}: {a} без аргумента"
            vals.append(argv[i + 1])
            i += 2
            continue
        matched = False
        for f in take_flags:
            if a.startswith(f + "="):
                vals.append(a.split("=", 1)[1])
                matched = True
                break
            if not f.startswith("--") and a.startswith(f) and len(a) > 2:
                vals.append(a[len(f):])
                matched = True
                break
        if matched:
            i += 1
            continue
        i += 1
    return vals, ""


# --- curl / docker (ядро) ----------------------------------------------------

def _url_host(url: str) -> str:
    u = url.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0]
    if "@" in u:
        u = u.rsplit("@", 1)[1]
    if u.startswith("["):
        return u[1:u.find("]")].lower() if "]" in u else ""
    return u.split(":", 1)[0].lower()


def _host_internal(host: str) -> bool:
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return "." not in host


def _curl(argv, internal_only, external_allow) -> tuple[bool, str]:
    urls, i = [], 1
    while i < len(argv):
        a = argv[i]
        if a.startswith("@"):
            return False, "curl: аргумент с '@' читает локальный файл — запрещено"
        if a in ("-X", "--request"):
            val = argv[i + 1] if i + 1 < len(argv) else ""
            if val.upper() not in ("GET", "HEAD"):
                return False, f"curl -X {val}: разрешён только GET/HEAD"
            i += 2
            continue
        if a.startswith("--request="):
            if a.split("=", 1)[1].upper() not in ("GET", "HEAD"):
                return False, "curl --request: разрешён только GET/HEAD"
            i += 1
            continue
        if a in CURL_LONG_VALUE or (a.startswith("--") and "=" in a and a.split("=", 1)[0] in CURL_LONG_VALUE):
            val = a.split("=", 1)[1] if "=" in a else (argv[i + 1] if i + 1 < len(argv) else "")
            if val.startswith("@"):
                return False, f"curl {a}: значение с '@' читает локальный файл — запрещено"
            i += 1 if "=" in a else 2
            continue
        if a in CURL_LONG_BOOL:
            i += 1
            continue
        if a.startswith("--"):
            return False, f"curl: флаг {a} не в allowlist безопасного GET (fail-closed)"
        if a.startswith("-") and len(a) >= 2:
            chars = a[1:]
            if all(c in CURL_SHORT_BOOL for c in chars):
                i += 1
                continue
            if len(chars) == 1 and chars in CURL_SHORT_VALUE:
                val = argv[i + 1] if i + 1 < len(argv) else ""
                if val.startswith("@"):
                    return False, f"curl -{chars}: значение с '@' читает локальный файл — запрещено"
                i += 2
                continue
            return False, f"curl: флаг {a} не в allowlist безопасного GET (fail-closed)"
        urls.append(a)
        i += 1
    if not urls:
        return False, "curl без URL"
    if internal_only:
        allow = [h.lower() for h in external_allow]
        for u in urls:
            host = _url_host(u)
            if not _host_internal(host) and host not in allow:
                return False, (
                    f"curl {host}: внешний хост запрещён (внутренняя сеть свободно, "
                    f"внешнее — только из external_allow профиля)"
                )
    return True, "curl GET"


def _docker_exec(rest, g) -> tuple[bool, str]:
    i = 0
    while i < len(rest):
        a = rest[i]
        flagname = a.split("=", 1)[0] if a.startswith("--") and "=" in a else a
        if flagname in EXEC_DENY_FLAGS:
            return False, f"docker exec {flagname} запрещён (эскалация/фоновый запуск)"
        if a in EXEC_VALUE_FLAGS:
            i += 2
            continue
        if a.startswith("--") and "=" in a:
            i += 1
            continue
        if a in EXEC_OK_BOOL_FLAGS:
            i += 1
            continue
        if a.startswith("-"):
            return False, f"docker exec: неизвестный флаг {a} (fail-closed)"
        positional = rest[i:]
        if len(positional) < 2:
            return False, "docker exec без контейнера и внутренней команды"
        inner = positional[1:]
        ok, reason = g.recurse(inner)
        if not ok:
            return False, f"docker exec: внутренняя команда не read-only — {reason}"
        return True, f"docker exec → {os.path.basename(inner[0])} (read)"
    return False, "docker exec без контейнера и внутренней команды"


def _docker(argv, reads, noun_reads, compose_reads, g) -> tuple[bool, str]:
    def compose(args):
        sub, rest = subcommand(args, COMPOSE_VALUE_FLAGS)
        if sub is None:
            return False, "docker compose без подкоманды"
        if sub not in compose_reads:
            return False, f"docker compose {sub}: не read-only"
        if sub == "logs" and has_follow(rest):
            return False, "docker compose logs -f стримит; используй --tail N"
        return True, f"docker compose {sub}"

    if os.path.basename(argv[0]) == "docker-compose":
        return compose(argv[1:])
    sub, rest = subcommand(argv[1:], DOCKER_VALUE_FLAGS)
    if sub is None:
        if any(a in ("--version", "-v") for a in argv[1:]):
            return True, "docker --version"
        return False, "docker без подкоманды"
    if sub == "compose":
        return compose(rest)
    if sub == "exec":
        return _docker_exec(rest, g)
    if sub in reads:
        if sub == "logs" and has_follow(rest):
            return False, "docker logs -f стримит; используй --tail N"
        if sub == "stats" and "--no-stream" not in rest:
            return False, "docker stats без --no-stream стримит; добавь --no-stream"
        return True, f"docker {sub}"
    if sub in noun_reads:
        verb, _ = subcommand(rest, set())
        if verb in noun_reads[sub]:
            return True, f"docker {sub} {verb}"
        return False, f"docker {sub} {verb}: не read-only (разрешены: {', '.join(noun_reads[sub])})"
    return False, f"docker {sub}: не read-only (разрешены: {', '.join(reads)}; exec с read-командой; compose)"


# --- generic verb+flag движок ------------------------------------------------

def _verbs(argv, *, allow=(), subreads=None, value_flags=(), deny_flags=(),
          allow_flags=None, require_flag=None, allow_bare=False,
          no_follow=False) -> tuple[bool, str]:
    name = os.path.basename(argv[0])
    rest = argv[1:]
    value_flags, deny_flags = set(value_flags), set(deny_flags)
    allow_up = {v.upper() for v in allow}
    subreads = {k.upper(): {s.upper() for s in v} for k, v in (subreads or {}).items()}
    seen_flags, positionals, i = [], [], 0
    while i < len(rest):
        a = rest[i]
        base = a.split("=", 1)[0]
        if base in deny_flags:
            return False, f"{name} {base}: запрещён (не read-only)"
        if no_follow and (a in ("-f", "-F", "--follow") or a.startswith("--follow=")
                          or (a.startswith("-") and not a.startswith("--") and "f" in a[1:])):
            return False, f"{name} -f стримит бесконечно — запрещено"
        if a in value_flags:
            i += 2
            continue
        if a.startswith("--") and "=" in a:
            i += 1
            continue
        if a.startswith("-"):
            if allow_flags is not None and base not in allow_flags:
                return False, f"{name}: разрешены только флаги {', '.join(allow_flags)}"
            seen_flags.append(base)
            i += 1
            continue
        positionals.append(a)
        i += 1

    if allow_up or subreads:
        if not positionals:
            if allow_bare:
                return True, f"{name} (read)"
            return False, f"{name}: нужна read-команда/подкоманда"
        verb = positionals[0].upper()
        if verb in subreads:
            if len(positionals) < 2:
                return False, f"{name} {verb}: нужна подкоманда ({', '.join(sorted(subreads[verb]))})"
            sub = positionals[1].upper()
            if sub not in subreads[verb]:
                return False, f"{name} {verb} {positionals[1]}: не read (разрешено: {', '.join(sorted(subreads[verb]))})"
            return True, f"{name} {verb} {sub} (read)"
        if verb in allow_up:
            return True, f"{name} {verb} (read)"
        return False, f"{name} {positionals[0]}: не read-only команда"

    # флаговый режим (nginx/crontab): позиционных быть не должно
    if positionals:
        return False, f"{name}: неожиданный аргумент {positionals[0]!r} (только чтение)"
    if allow_flags is not None:
        if not seen_flags:
            return False, f"{name}: нужен read-флаг ({', '.join(allow_flags)})"
        if require_flag and require_flag not in seen_flags:
            return False, f"{name}: обязателен {require_flag}"
    return True, f"{name} (read)"


# --- локальные read-утилиты (для профиля local) ------------------------------

def _read_util(argv, read_commands) -> tuple[bool, str]:
    name = os.path.basename(argv[0])
    if name == "tail" and tail_follows(argv[1:]):
        return False, "tail -f/-F/--follow повесит агента; используй -n/--lines"
    if name == "journalctl":
        if has_follow(argv[1:]):
            return False, "journalctl -f/--follow повесит агента; используй --since/-n"
        vac = {"--vacuum-time", "--vacuum-size", "--vacuum-files", "--rotate",
               "--flush", "--sync", "--relinquish-var", "--update-catalog", "--setup-keys"}
        if any(a.split("=", 1)[0] in vac for a in argv[1:]):
            return False, "journalctl --vacuum/--rotate/--flush меняет журнал — запрещено"
    if name == "ss" and any(a in ("-K", "--kill") for a in argv[1:]):
        return False, "ss -K/--kill закрывает сокеты — запрещено"
    if name == "netstat" and any(a in ("-c", "--continuous") for a in argv[1:]):
        return False, "netstat -c/--continuous стримит бесконечно; убери флаг"
    if name == "date" and any(a in ("-s", "--set") or a.startswith("--set=") for a in argv[1:]):
        return False, "date -s/--set меняет системные часы — запрещено"
    if name == "sort" and any(a in ("-o", "--output") or a.startswith(("-o", "--output=")) for a in argv[1:]):
        return False, "sort -o/--output пишет в файл — запрещено"
    if name == "tree" and any(a in ("-o", "--output") or a.startswith(("-o", "--output=")) for a in argv[1:]):
        return False, "tree -o пишет вывод в файл — запрещено"
    if name == "uniq":
        vf = {"-f", "--skip-fields", "-s", "--skip-chars", "-w", "--check-chars"}
        pos, i = [], 1
        while i < len(argv):
            a = argv[i]
            if a in vf:
                i += 2
                continue
            if a.startswith("-"):
                i += 1
                continue
            pos.append(a)
            i += 1
        if len(pos) >= 2:
            return False, "uniq c двумя файлами пишет во второй — запрещено"
    if name == "find":
        bad = ("-delete", "-exec", "-execdir", "-ok", "-okdir",
               "-fprint", "-fprint0", "-fprintf", "-fls")
        if any(a in bad for a in argv):
            return False, "find с -delete/-exec*/-ok*/-fprint* запрещён"
        return True, "find (read)"
    if name == "yq":
        bad = {"-i", "--inplace", "--in-place", "-s", "--split-exp"}
        if any(a in bad or a.split("=", 1)[0] in bad for a in argv[1:]):
            return False, "yq -i/--split-exp пишет файлы — запрещено"
        return True, "yq (read)"
    if name in read_commands:
        return True, f"{name} (read)"
    return False, f"команда '{name}' не в allowlist read-команд"


# --- тулкит g для профилей ---------------------------------------------------

class Toolkit:
    """API хелперов, передаётся в profile.check(argv, g). Инкапсулирует разбор ядра."""

    def __init__(self, depth: int):
        self.depth = depth

    def name(self, argv):
        return os.path.basename(argv[0])

    def subcommand(self, argv, value_flags=()):
        return subcommand(argv[1:], set(value_flags))

    def verbs(self, argv, **kw):
        return _verbs(argv, **kw)

    def sql(self, argv, *, cmd_flags, file_flags=(), allow_prefixes, forbid):
        vals, err = _extract_args(argv, set(cmd_flags), set(file_flags), self.name(argv))
        if err:
            return False, err
        if not vals:
            return False, f'{self.name(argv)}: интерактив запрещён — используй {cmd_flags[0]} "SELECT …"'
        if len(vals) > 1:
            return False, "несколько инструкций запрещено (одна на запрос)"
        return sql_read_guard(vals[0], allow_prefixes, forbid)

    def mongo(self, argv, *, eval_flags, file_flags=(), forbid):
        vals, err = _extract_args(argv, set(eval_flags), set(file_flags), self.name(argv))
        if err:
            return False, err
        if not vals:
            return False, 'mongosh: интерактив/скрипт запрещён — используй --eval "db.coll.find(...)"'
        if len(vals) > 1:
            return False, "несколько --eval запрещено (одна инструкция на запрос)"
        low = vals[0].lower()
        for kw in forbid:
            if kw.lower() in low:
                return False, f"mongo: запрещённый метод/оператор: {kw}"
        return True, "mongo read-only (--eval)"

    def curl(self, argv, *, internal_only=True, external_allow=()):
        return _curl(argv, internal_only, list(external_allow))

    def docker(self, argv, *, reads, noun_reads=None, compose_reads=()):
        return _docker(argv, reads, noun_reads or {}, compose_reads, self)

    def read_util(self, argv, read_commands):
        return _read_util(argv, set(read_commands))

    def recurse(self, argv):
        return dispatch(argv, self.depth + 1)


# --- загрузка профилей-модулей -----------------------------------------------

_registry: dict | None = None


def _load_profiles() -> dict:
    """Импорт profiles/*.py → {cmd_of: команда→id, mod_of: id→модуль, fallback}."""
    cmd_of, mod_of, fallback = {}, {}, None
    for f in sorted(PROFILES.glob("*.py")):
        if f.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(f"srvx_profile_{f.stem}", f)
        if not spec or not spec.loader:
            continue
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:  # noqa: BLE001 — битый профиль не должен ронять гард
            continue
        pid = getattr(mod, "ID", None)
        if not pid or not callable(getattr(mod, "check", None)):
            continue
        mod_of[pid] = mod
        if getattr(mod, "FALLBACK", False):
            fallback = mod
        for cmd in getattr(mod, "COMMANDS", []):
            cmd_of[cmd] = pid
    return {"cmd_of": cmd_of, "mod_of": mod_of, "fallback": fallback}


def registry() -> dict:
    global _registry
    if _registry is None:
        _registry = _load_profiles()
    return _registry


def profile_enabled(pid: str) -> bool:
    # Читаем свежим на каждый вызов: тумблер из админки действует без рестарта.
    # Файл крохотный; реестр модулей при этом кешируется (импорт один раз).
    try:
        toggles = json.loads(Path(STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        toggles = {}
    return bool(toggles.get(pid, True))  # по умолчанию включён


# --- диспетчер ---------------------------------------------------------------

def dispatch(argv: list[str], depth: int) -> tuple[bool, str]:
    if not argv:
        return False, "пустая команда"
    for tok in argv[1:]:
        if forbidden_path(tok):
            return False, f"чтение спецфайла {tok} запрещено (сырое устройство/бесконечный источник)"
    reg = registry()
    name = os.path.basename(argv[0])
    pid = reg["cmd_of"].get(name)
    g = Toolkit(depth)
    if pid:
        if not profile_enabled(pid):
            return False, f"профиль '{pid}' выключен — {name} запрещён"
        return reg["mod_of"][pid].check(argv, g)
    fb = reg["fallback"]
    if fb is None:
        return False, f"команда '{name}' не покрыта ни одним профилем"
    if not profile_enabled(fb.ID):
        return False, f"профиль '{fb.ID}' (базовая оболочка) выключен — {name} запрещён"
    return fb.check(argv, g)


def check_command_string(command: str, depth: int = 0) -> tuple[bool, str]:
    if depth > 4:
        return False, "слишком глубокая вложенность команд"
    for m in DANGEROUS:
        if m in command:
            return False, f"запрещённый метасимвол: {m!r} (запись/подстановка/цепочка)"
    for stage in command.split("|"):
        stage = stage.strip()
        if not stage:
            return False, "пустой сегмент пайпа"
        try:
            argv = shlex.split(stage, posix=True)
        except ValueError as e:
            return False, f"не удалось разобрать команду: {e}"
        ok, reason = dispatch(argv, depth)
        if not ok:
            return ok, reason
    return True, "read-only pipeline"


# --- hook I/O ----------------------------------------------------------------

def emit(decision: str, reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except ValueError:
        print("srv-explore guard: не разобран PreToolUse JSON — блок (fail-closed)", file=sys.stderr)
        return 2
    if payload.get("tool_name") != "Bash":
        return 0
    command = (payload.get("tool_input") or {}).get("command", "")
    if not command.strip():
        print("srv-explore guard: пустая команда", file=sys.stderr)
        return 2
    ok, reason = check_command_string(command)
    if ok:
        emit("allow", reason)
        return 0
    emit("deny", reason)
    print(
        f"srv-explore guard: заблокировано — {reason}. Разрешено только чтение. "
        f"Не обходи (sh -c/base64/файлы) — переформулируй как чтение или предложи действие текстом.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
