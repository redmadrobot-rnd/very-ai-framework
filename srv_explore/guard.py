#!/usr/bin/env python3
"""srv-explore — PreToolUse-гард + аудит.

Вешается в frontmatter субагента на инструмент Bash. Читает PreToolUse-JSON со
stdin, сверяет команду с allowlist (только чтение), пишет строку в аудит-лог ДО
решения и возвращает:
    allow → JSON permissionDecision=allow, exit 0;
    deny  → причина в stderr + JSON permissionDecision=deny, exit 2 (hard block).

Fail-closed: любая неоднозначность/ошибка разбора → deny + exit 2. Настоящая
граница для БД — read-only роль СУБД; этот гард — defense-in-depth поверх неё
и единственный барьер для shell-команд (docker/ssh/curl/systemctl).

Философия: default-deny. Разрешить имя команды НЕДОСТАТОЧНО — у многих «read»-утилит
есть флаги записи/исполнения/эксфильтрации (sort -o, curl -D, ssh -o ProxyCommand,
date -s). Поэтому: allowlist имён + per-command проверка опасных флагов + allowlist
флагов для curl/ssh + запрет чтения /dev/* (сырой/бесконечный источник). docker exec
допускается, но вложенная команда рекурсивно проверяется тем же allowlist.

Конфиг рядом: profiles/shell.json (read-команды/подкоманды), profiles/<db>.json
(SQL-диалекты). Ядро СУБД-агностично: новый диалект = новый JSON.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # Windows-консоль (cp1251) иначе бьёт кириллицу
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
PROFILES = HERE / "profiles"

# Метасимволы записи/сайд-эффекта/подстановки/цепочки. Пайп (|) обрабатывается
# отдельно — read-пайплайны легитимны, каждый сегмент проверяется по-своему.
DANGEROUS = ["`", "$(", ">", "<", ";", "&", "\n", "\r"]

DB_CLIENTS = ("psql", "mysql", "mongosh", "clickhouse-client", "redis-cli")

# Спецфайлы, чтение которых блокируем: сырые устройства и бесконечные источники
# (/dev/zero|random|sd*|mem…) вешают/эксфильтрируют. Разрешаем только безобидные.
SAFE_DEV = {"/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/tty"}

DOCKER_VALUE_FLAGS = {
    "-H", "--host", "--context", "--config", "-l", "--log-level",
    "--tlscacert", "--tlscert", "--tlskey",
}
COMPOSE_VALUE_FLAGS = {
    "-f", "--file", "-p", "--project-name", "--project-directory",
    "--env-file", "--profile", "--ansi", "--progress",
}
SYSTEMCTL_VALUE_FLAGS = {
    "-H", "--host", "-M", "--machine", "-t", "--type", "--state",
    "-p", "--property", "--job-mode", "--kill-whom", "--signal",
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

# ssh — allowlist безопасных флагов. Исключены -o (ProxyCommand/LocalCommand!), -F
# (кастомный конфиг → ProxyCommand), -L/-R/-D/-W/-J (туннели), -E (лог в файл).
SSH_BOOL_FLAGS = {"-q", "-T", "-C", "-4", "-6", "-v", "-x"}
SSH_VALUE_FLAGS = {"-p", "-i", "-l", "-c", "-m", "-b"}


def audit(session: str, command: str, decision: str, reason: str) -> None:
    log_path = os.environ.get("SRV_EXPLORE_AUDIT") or str(
        HERE / "audit" / f"explore-{datetime.now(timezone.utc):%Y%m%d}.log"
    )
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": session,
            "command": command,
            "decision": decision,
            "reason": reason,
        }
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass  # аудит не должен ронять гард, но и не должен молча открывать запись


def load_json(name: str) -> dict:
    try:
        return json.loads((PROFILES / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def has_follow(argv: list[str]) -> bool:
    for a in argv:
        if a in FOLLOW_FLAGS or a.startswith("--follow="):
            return True
        # слитные короткие: docker logs -ft, journalctl -fu (-F в journalctl = field, не follow)
        if a.startswith("-") and not a.startswith("--") and "f" in a[1:]:
            return True
    return False


def tail_follows(argv: list[str]) -> bool:
    """tail: -f, -F (= -f --retry), --follow, --follow=name, и комбинированные
    короткие вроде -fn10. Любой стример повесит агента."""
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
    """Первый позиционный токен (подкоманда) и хвост после него, пропуская
    глобальные флаги и их значения. None → подкоманды нет (одни флаги)."""
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


# --- SQL ---------------------------------------------------------------------

def sql_read_guard(sql: str, profile: dict) -> tuple[bool, str]:
    body = re.sub(r"--[^\n]*", " ", sql)
    body = re.sub(r"/\*.*?\*/", " ", body, flags=re.S).strip().strip(";").strip()
    if not body:
        return False, "пустой SQL"
    if ";" in body:
        return False, "несколько стейтментов запрещено (одна инструкция на запрос)"
    first = body.split(None, 1)[0].lower()
    allow = [p.lower() for p in profile.get("allow_prefixes", [])]
    if first not in allow:
        return False, f"стейтмент '{first}' не read-only (разрешено: {', '.join(allow)})"
    if re.search(r"\bexplain\b", body, re.I) and re.search(r"\banalyze\b", body, re.I):
        return False, "EXPLAIN ANALYZE выполняет запрос — запрещено"
    for kw in profile.get("forbid_keywords", []):
        if re.search(rf"\b{re.escape(kw)}\b", body, re.I):
            return False, f"запрещённое ключевое слово/функция: {kw}"
    return True, "sql read-only"


def find_client_profile(client: str, kind: str | None = None) -> dict | None:
    """Профиль по имени клиента; если задан kind — ещё и по типу диалекта."""
    for f in PROFILES.glob("*.json"):
        if f.name == "shell.json":
            continue
        prof = load_json(f.name)
        if prof.get("client") == client and (kind is None or prof.get("kind") == kind):
            return prof
    return None


def check_db_client(argv: list[str]) -> tuple[bool, str]:
    """Диспатч по kind профиля клиента: sql / mongo / redis."""
    client = os.path.basename(argv[0])
    prof = find_client_profile(client)
    if not prof:
        return False, f"нет профиля для клиента {client}"
    kind = prof.get("kind")
    if kind == "sql":
        return check_sql_client(argv, prof)
    if kind == "mongo":
        return check_mongo_client(argv, prof)
    if kind == "redis":
        return check_redis_client(argv, prof)
    return False, f"клиент {client}: профиль kind={kind!r} не поддержан гардом"


def check_sql_client(argv: list[str], prof: dict) -> tuple[bool, str]:
    cmds = []
    i = 1
    while i < len(argv):
        a = argv[i]
        # SQL из файла (все формы: -f x, --file x, --file=x, слитная -f/path) — не проверяется гардом
        if a in ("-f", "--file") or a.startswith("--file=") or (a.startswith("-f") and len(a) > 2):
            return False, "-f/--file (SQL из файла) не проверяется гардом — запрещено"
        if a in ("-c", "--command"):
            if i + 1 >= len(argv):
                return False, "-c/--command без аргумента"
            cmds.append(argv[i + 1])
            i += 2
            continue
        if a.startswith("--command="):
            cmds.append(a.split("=", 1)[1])
        elif a.startswith("-c") and len(a) > 2:  # слитная форма -cSELECT…
            cmds.append(a[2:])
        i += 1
    if not cmds:
        return False, 'интерактивный режим клиента БД запрещён — используй -c "SELECT …"'
    if len(cmds) > 1:
        # psql исполняет КАЖДЫЙ -c по порядку — проверить только последний нельзя
        return False, "несколько -c/--command запрещено (одна инструкция на запрос)"
    return sql_read_guard(cmds[0], prof)


# --- MongoDB (mongosh --eval) ------------------------------------------------

def mongo_read_guard(js: str, prof: dict) -> tuple[bool, str]:
    body = js.strip()
    if not body:
        return False, "пустой --eval"
    # Мутирующие методы/стадии/команды ловим подстрокой: имена характерны, а $out/$merge
    # не ловятся \b (символ $). Честный агент читает — этого достаточно как defense.
    low = body.lower()
    for kw in prof.get("forbid_keywords", []):
        if kw.lower() in low:
            return False, f"mongo: запрещённый метод/оператор: {kw}"
    return True, "mongo read-only (--eval)"


def check_mongo_client(argv: list[str], prof: dict) -> tuple[bool, str]:
    evals: list[str] = []
    i = 1
    while i < len(argv):
        a = argv[i]
        # скрипт из файла не проверяется гардом
        if a in ("-f", "--file") or a.startswith("--file=") or (a.startswith("-f") and len(a) > 2):
            return False, "mongosh --file (скрипт из файла) не проверяется гардом — запрещено"
        if a in ("--eval", "-e"):
            if i + 1 >= len(argv):
                return False, "mongosh --eval без аргумента"
            evals.append(argv[i + 1])
            i += 2
            continue
        if a.startswith("--eval="):
            evals.append(a.split("=", 1)[1])
        i += 1
    if not evals:
        return False, 'mongosh: интерактив/скрипт запрещён — используй --eval "db.coll.find(...)"'
    if len(evals) > 1:
        return False, "несколько --eval запрещено (одна инструкция на запрос)"
    return mongo_read_guard(evals[0], prof)


# --- Redis (redis-cli) -------------------------------------------------------

def check_redis_client(argv: list[str], prof: dict) -> tuple[bool, str]:
    value_flags = set(prof.get("value_flags", []))
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("--eval", "-x", "--pipe", "--pipe-mode"):
            return False, f"redis-cli {a}: Lua/stdin/pipe-режим запрещён"
        if a in value_flags:
            i += 2
            continue
        if a.startswith("--") and "=" in a and a.split("=", 1)[0] in value_flags:
            i += 1
            continue
        if a.startswith("-"):
            # прочие флаги подключения (--tls, --no-auth-warning, -3, …) — булевы, пропускаем
            i += 1
            continue
        # первый позиционный — это команда
        verb = a.upper()
        rest = argv[i + 1:]
        sub_reads = {k.upper(): [s.upper() for s in v]
                     for k, v in prof.get("subcommand_reads", {}).items()}
        if verb in sub_reads:
            if not rest:
                return False, f"redis-cli {verb}: нужна read-подкоманда ({', '.join(sub_reads[verb])})"
            sub = rest[0].upper()
            if sub not in sub_reads[verb]:
                return False, f"redis-cli {verb} {rest[0]}: не read (разрешено: {', '.join(sub_reads[verb])})"
            return True, f"redis {verb} {sub} (read)"
        if verb in [c.upper() for c in prof.get("allow_commands", [])]:
            return True, f"redis {verb} (read)"
        return False, f"redis-cli {verb}: не read-only команда (не в allowlist)"
    return False, 'redis-cli: интерактив без команды запрещён — используй "GET key" и т.п.'


# --- curl (allowlist безопасного GET) ----------------------------------------

def check_curl(argv: list[str]) -> tuple[bool, str]:
    i = 1
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
        i += 1  # позиционный (URL)
    return True, "curl GET"


# --- docker ------------------------------------------------------------------

def check_docker_exec(rest: list[str], shell: dict, depth: int) -> tuple[bool, str]:
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
        ok, reason = check_simple(inner, shell, depth + 1)
        if not ok:
            return False, f"docker exec: внутренняя команда не read-only — {reason}"
        return True, f"docker exec → {os.path.basename(inner[0])} (read)"
    return False, "docker exec без контейнера и внутренней команды"


def check_compose(args: list[str], shell: dict) -> tuple[bool, str]:
    sub, rest = subcommand(args, COMPOSE_VALUE_FLAGS)
    if sub is None:
        return False, "docker compose без подкоманды"
    if sub not in shell.get("docker_compose_read_subcommands", []):
        return False, f"docker compose {sub}: не read-only"
    if sub == "logs" and has_follow(rest):
        return False, "docker compose logs -f стримит бесконечно; используй --tail N"
    return True, f"docker compose {sub}"


def check_docker(argv: list[str], shell: dict, depth: int) -> tuple[bool, str]:
    binary = os.path.basename(argv[0])
    if binary == "docker-compose":
        return check_compose(argv[1:], shell)
    sub, rest = subcommand(argv[1:], DOCKER_VALUE_FLAGS)
    if sub is None:
        return False, "docker без подкоманды"
    if sub == "compose":
        return check_compose(rest, shell)
    if sub == "exec":
        return check_docker_exec(rest, shell, depth)
    if sub in shell.get("docker_read_subcommands", []):
        if sub == "logs" and has_follow(rest):
            return False, "docker logs -f стримит бесконечно; используй --tail N"
        if sub == "stats" and "--no-stream" not in rest:
            return False, "docker stats без --no-stream стримит; добавь --no-stream"
        return True, f"docker {sub}"
    allowed = ", ".join(shell.get("docker_read_subcommands", []))
    return False, f"docker {sub}: не read-only (разрешены: {allowed}; exec с read-командой; compose)"


# --- systemctl / ssh ---------------------------------------------------------

def check_systemctl(argv: list[str], shell: dict) -> tuple[bool, str]:
    sub, _ = subcommand(argv[1:], SYSTEMCTL_VALUE_FLAGS)
    if sub is None:
        return True, "systemctl (list-units, read)"
    if sub in shell.get("systemctl_read_subcommands", []):
        return True, f"systemctl {sub}"
    return False, f"systemctl {sub}: не read-only"


def check_rabbitmqctl(argv: list[str]) -> tuple[bool, str]:
    prof = find_client_profile("rabbitmqctl", kind="verb")
    if not prof:
        return False, "нет профиля rabbitmq"
    sub, _ = subcommand(argv[1:], set(prof.get("value_flags", [])))
    if sub is None:
        return False, "rabbitmqctl без подкоманды"
    if sub in prof.get("read_subcommands", []):
        return True, f"rabbitmqctl {sub} (read)"
    return False, f"rabbitmqctl {sub}: не read-only подкоманда"


def check_ssh(argv: list[str], shell: dict, depth: int) -> tuple[bool, str]:
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in SSH_VALUE_FLAGS:
            i += 2
            continue
        if a in SSH_BOOL_FLAGS:
            i += 1
            continue
        if a.startswith("-"):
            return False, (
                f"ssh: флаг {a} запрещён (разрешены -p/-i/-l/-c/-m/-b/-q/-T/-C/-4/-6; "
                "-o/-F/-L/-R/-D/-W/-J исключены — ProxyCommand/туннели = локальное исполнение)"
            )
        remote = argv[i + 1:]
        if not remote:
            return False, "ssh без удалённой команды (интерактив/туннель запрещён)"
        return check_command_string(" ".join(remote), shell, depth + 1)
    return False, "ssh без хоста"


# --- generic -----------------------------------------------------------------

def check_simple(argv: list[str], shell: dict, depth: int) -> tuple[bool, str]:
    if not argv:
        return False, "пустая команда"
    name = os.path.basename(argv[0])
    for tok in argv[1:]:
        if forbidden_path(tok):
            return False, f"чтение спецфайла {tok} запрещено (сырое устройство/бесконечный источник)"
    # On-host сервис читает локально; egress закрыт, чтобы инъекция не увела данные
    # наружу. DB-клиенты не режем — чтение БД идёт в readonly-роль, это цель.
    if name in ("curl", "ssh") and os.environ.get("SRV_EXPLORE_NO_NETWORK"):
        return False, f"{name}: сетевые команды отключены (SRV_EXPLORE_NO_NETWORK) — egress закрыт"
    if name in DB_CLIENTS:
        return check_db_client(argv)
    if name == "curl":
        return check_curl(argv)
    if name in ("docker", "docker-compose"):
        return check_docker(argv, shell, depth)
    if name == "systemctl":
        return check_systemctl(argv, shell)
    if name == "rabbitmqctl":
        return check_rabbitmqctl(argv)
    if name == "ssh":
        return check_ssh(argv, shell, depth)
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
        value_flags = {"-f", "--skip-fields", "-s", "--skip-chars", "-w", "--check-chars"}
        pos, i = [], 1
        while i < len(argv):
            a = argv[i]
            if a in value_flags:
                i += 2
                continue
            if a.startswith("-"):
                i += 1
                continue
            pos.append(a)
            i += 1
        if len(pos) >= 2:
            return False, "uniq c двумя файловыми аргументами пишет во второй файл — запрещено"
    if name == "find":
        write_actions = ("-delete", "-exec", "-execdir", "-ok", "-okdir",
                         "-fprint", "-fprint0", "-fprintf", "-fls")
        if any(a in write_actions for a in argv):
            return False, "find с -delete/-exec*/-ok*/-fprint* запрещён"
        return True, "find (read)"
    if name == "yq":
        bad = {"-i", "--inplace", "--in-place", "-s", "--split-exp"}
        if any(a in bad or a.split("=", 1)[0] in bad for a in argv[1:]):
            return False, "yq -i/--split-exp пишет файлы — запрещено"
        return True, "yq (read)"
    if name in shell.get("read_commands", []):
        return True, f"{name} (read)"
    return False, f"команда '{name}' не в allowlist read-команд"


def check_command_string(command: str, shell: dict, depth: int = 0) -> tuple[bool, str]:
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
        ok, reason = check_simple(argv, shell, depth)
        if not ok:
            return ok, reason
    return True, "read-only pipeline"


# --- hook I/O ----------------------------------------------------------------

def emit(decision: str, reason: str) -> None:
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(out, ensure_ascii=False))


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except ValueError:
        print("srv-explore guard: не разобран PreToolUse JSON — блок (fail-closed)", file=sys.stderr)
        return 2
    session = payload.get("session_id", "-")
    if payload.get("tool_name") != "Bash":
        return 0
    command = (payload.get("tool_input") or {}).get("command", "")
    if not command.strip():
        audit(session, command, "deny", "пустая команда")
        print("srv-explore guard: пустая команда", file=sys.stderr)
        return 2
    shell = load_json("shell.json")
    ok, reason = check_command_string(command, shell)
    audit(session, command, "allow" if ok else "deny", reason)
    if ok:
        emit("allow", reason)
        return 0
    emit("deny", reason)
    print(
        f"srv-explore guard: заблокировано — {reason}. Разрешено только чтение из allowlist. "
        f"Не обходи (sh -c/base64/файлы) — переформулируй как чтение или предложи действие инженеру текстом.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
