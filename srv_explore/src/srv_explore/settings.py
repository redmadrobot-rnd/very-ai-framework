"""Настройки, которые админ правит из UI: объявленный набор ручек, не свободный env.

Ручка = запись в `FIELDS` (тот же контракт, что у формы плагина, ядро рендерит её
тем же кодом) + валидатор. Значения лежат в `settings.json` рядом с остальным
состоянием, 0600. Секретов здесь нет и быть не должно: токен модели и админ-токен
живут в `/etc/srv-explore/env` и приезжают деплоем.

Egress складывается из двух уровней, оба аддитивные:
  - деплойный: `SRV_EXPLORE_TRUSTED_DOMAINS` / `_CIDRS` в env, под ревью в git;
  - здешний: правится в админке, применяется сразу.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from srv_explore.plugin_api import field

CFG_DIR = Path(os.environ.get("SRV_EXPLORE_CFG_DIR", "/etc/srv-explore"))
PROXY_ALLOW = CFG_DIR / "proxy-allow"  # то, что читает tinyproxy
PROXY_ALLOW_BASE = CFG_DIR / "proxy-allow.base"  # пишет install.sh (домены из env)
MODEL_DOMAIN = "api.anthropic.com"  # без него агент не доживёт до первой команды

FIELDS = [
    field(
        "egress_domains",
        "Домены наружу (через прокси)",
        "grafana.example.com, s3.internal",
        required=False,
        hint=(
            "агент ходит на них через форвард-прокси, только CONNECT 443; "
            f"поддомены включены. {MODEL_DOMAIN} открыт всегда"
        ),
    ),
    field(
        "egress_cidrs",
        "Подсети и адреса напрямую",
        "203.0.113.10/32, 198.51.100.0/24",
        required=False,
        hint=(
            "в обход прокси, на уровне ядра. Loopback и приватные сети "
            "(10/8, 172.16/12, 192.168/16) открыты и так; 0.0.0.0/0 не принимается"
        ),
    ),
]

_HOST_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")


def store_path() -> Path:
    """Рядом с остальным состоянием — путь задаёт та же переменная, что у плагинов."""
    state = os.environ.get(
        "SRV_EXPLORE_PLUGIN_STATE", "/var/lib/srv-explore/plugins.json"
    )
    return Path(state).with_name("settings.json")


def load() -> dict[str, str]:
    try:
        data = json.loads(store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {f["name"]: str(data.get(f["name"], "")) for f in FIELDS}


def save(values: dict[str, str]) -> dict[str, str]:
    """Сохранить провалидированное. Возвращает то, что легло на диск."""
    clean = {f["name"]: str(values.get(f["name"], "")).strip() for f in FIELDS}
    path = store_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return clean


# --- разбор и валидация -------------------------------------------------------


def split_list(raw: str) -> list[str]:
    """Список через запятую, пробел или перевод строки. Без дублей, порядок сохранён."""
    out: list[str] = []
    for item in re.split(r"[,\s]+", (raw or "").strip()):
        if item and item not in out:
            out.append(item)
    return out


def parse_domains(raw: str) -> tuple[list[str], str]:
    """(домены, ошибка). Ошибка непустая — значение не принимаем целиком."""
    domains = []
    for d in split_list(raw):
        host = d.strip().strip(".").lower()
        if not _HOST_RE.match(host):
            return [], f"не похоже на домен: {d}"
        domains.append(host)
    return domains, ""


def parse_cidrs(raw: str) -> tuple[list[str], str]:
    nets = []
    for c in split_list(raw):
        try:
            net = ipaddress.ip_network(c, strict=False)
        except ValueError as e:
            return [], f"не разбирается как адрес или подсеть: {c} ({e})"
        if net.prefixlen == 0:
            # 0.0.0.0/0 или ::/0 — это не «подсеть», а снятие egress-firewall
            # целиком, в обход доменного allowlist. Опечатка не должна такое уметь.
            return [], f"{c} открывает весь интернет — так нельзя, перечисли подсети"
        nets.append(str(net))
    return nets, ""


def validate(values: dict[str, str]) -> str:
    """Пустая строка — всё в порядке, иначе текст ошибки для админки."""
    checks = ((parse_domains, "egress_domains"), (parse_cidrs, "egress_cidrs"))
    for parse, key in checks:
        _, err = parse(values.get(key, ""))
        if err:
            return err
    return ""


# --- эффективные значения (env + настройки) -----------------------------------


def _env_list(name: str) -> list[str]:
    return split_list(os.environ.get(name, ""))


def egress_cidrs() -> list[str]:
    """Что добавить в IPAddressAllow песочницы. Читается на каждом спавне агента."""
    nets, _ = parse_cidrs(load().get("egress_cidrs", ""))
    return _env_list("SRV_EXPLORE_TRUSTED_CIDRS") + nets


def egress_domains() -> list[str]:
    doms, _ = parse_domains(load().get("egress_domains", ""))
    return _env_list("SRV_EXPLORE_TRUSTED_DOMAINS") + doms


def allow_file_text(domains: list[str]) -> str:
    """Содержимое фильтра tinyproxy: extended-regex на домен и его поддомены."""
    lines = []
    for d in [MODEL_DOMAIN, *domains]:
        if d and d not in lines:
            lines.append(d)
    return "".join(f"(^|\\.){re.escape(d)}$\n" for d in lines)


def apply_egress() -> str:
    """Собрать allowlist прокси и перезапустить его. Возвращает короткий отчёт.

    Базу пишет install.sh (домены из env), поверх кладутся домены из настроек —
    поэтому деплой не затирает то, что задано в админке, и наоборот.
    """
    try:  # база — плоский список доменов, по одному на строку
        base = split_list(PROXY_ALLOW_BASE.read_text(encoding="utf-8"))
    except OSError:
        base = []  # базы нет (dev, первый запуск) — обойдёмся env + настройками
    text = allow_file_text(base + egress_domains())
    try:
        PROXY_ALLOW.write_text(text, encoding="utf-8")
        os.chmod(PROXY_ALLOW, 0o644)
    except OSError as e:
        return f"allowlist не записан: {e}"
    rc = subprocess.run(  # noqa: S603 — фиксированный argv
        ["systemctl", "restart", "srv-explore-proxy.service"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if rc.returncode != 0:
        return f"прокси не перезапущен: {rc.stderr.strip()[:120]}"
    return f"домены: {len(text.splitlines())}"


def main() -> int:
    """`python -m srv_explore.settings` — применить настройки (зовёт install.sh)."""
    print(apply_egress(), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
