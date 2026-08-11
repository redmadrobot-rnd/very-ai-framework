"""srv-explore — PreToolUse-гигиена. Универсальный, без доменных знаний и профилей.

Read-only держит РЕСУРС-СЛОЙ (RO-FS, egress-firewall, unprivileged-юзер, read-only
роли БД, docker-socket-proxy) — см. README. Гард команды НЕ регулирует по существу;
это дешёвая гигиена + бэкстоп:
  - метасимволы записи/подстановки/цепочки (`>`/`;`/`&`/`$()`) — понятный deny вместо
    EROFS-крэша и страховка, если RO-FS кто-то не включил;
  - чтение спецфайлов /dev/* (сырой диск/бесконечный источник).
Всё прочее — allow (разрулит ресурс-слой).

Зовётся in-process из agent_worker (PreToolUse-хук SDK) — единственный потребитель.
"""

from __future__ import annotations

import shlex

# Метасимволы записи/подстановки/цепочки; пайп (|) разрешён (read-пайплайны).
# Причина отказа адресная: агент её читает и должен понять, ЧТО менять, иначе
# повторяет ту же форму команды раз за разом.
DANGEROUS = {
    "\n": "перевод строки внутри команды — собери всё в одну строку",
    "\r": "перевод строки внутри команды — собери всё в одну строку",
    ">": "редирект: писать нельзя, и для отбрасывания stderr он не нужен",
    "<": "редирект: передай файл аргументом, а не через <",
    ";": "цепочка команд — подавай по одной за раз",
    "&": "цепочка или фон — подавай команды по одной за раз",
    "`": "подстановка команды — посчитай значение отдельным шагом",
    "$(": "подстановка команды — посчитай значение отдельным шагом",
}
SAFE_DEV = {"/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr", "/dev/tty"}
# Дамп окружения. Гигиена, а не барьер: в env агента лежат токен модели и креды
# плагинов, и печатать их по первой просьбе не надо. Обойти можно (см. README).
DUMP_ENV = {"env", "printenv", "set", "export", "declare"}


def forbidden_path(tok: str) -> bool:
    p = tok.split("=", 1)[-1].strip("\"'") if "=" in tok else tok
    if p in SAFE_DEV or p.startswith("/dev/fd/"):
        return False
    if p.startswith("/dev/") or p.startswith("/proc/kcore"):
        return True
    # /proc: environ достаётся не только прямым путём `/proc/self/environ`, но и
    # обходом каталога процесса (`grep -R /proc/self`, Grep path=/proc). Режем:
    #   - любой путь, оканчивающийся на environ (прямое чтение);
    #   - каталог процесса как цель (весь /proc, /proc/self, /proc/<pid>) — рекурсия
    #     оттуда дойдёт до environ.
    # Конкретные безопасные файлы (/proc/self/cmdline, /proc/meminfo, /proc/net/*)
    # остаются читаемыми.
    parts = p.strip("/").split("/")
    if parts and parts[0] == "proc":
        if len(parts) == 1 or parts[-1] == "environ":
            return True  # весь /proc, или прямой environ любого процесса
        if len(parts) == 2 and (
            parts[1] in ("self", "thread-self") or parts[1].isdigit()
        ):
            return True  # каталог процесса как цель рекурсии
    return False


def check_file_path(path: str) -> tuple[bool, str]:
    """Гигиена для файловых инструментов (Read/Grep/Glob): те же спецфайлы, что
    режет Bash-путь. Иначе агент прочитал бы /proc/self/environ мимо гарда и достал
    токен модели с кредами прямо из окружения воркера."""
    if path and forbidden_path(path.strip()):
        return False, (
            f"{path}: спецфайл (устройство, поток, окружение) — "
            "бери нужное обычными путями"
        )
    return True, "ok"


def check_command_string(command: str) -> tuple[bool, str]:
    for m, why in DANGEROUS.items():
        if m in command:
            return False, why
    for stage in command.split("|"):
        stage = stage.strip()
        if not stage:
            return False, "пустой сегмент пайпа"
        try:
            argv = shlex.split(stage, posix=True)
        except ValueError as e:
            return False, f"не удалось разобрать команду: {e}"
        if argv and argv[0] in DUMP_ENV:
            return False, (
                f"{argv[0]}: в окружении креды, читать его нельзя — подставляй "
                'переменную как "$ИМЯ", не разглядывая значение'
            )
        for tok in argv:
            if forbidden_path(tok):
                return False, (
                    f"{tok}: спецфайл (устройство, поток, окружение) — "
                    "бери нужное обычными утилитами"
                )
    return True, "read-only ok"
