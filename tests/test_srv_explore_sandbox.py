"""Сборка команды песочницы. Systemd-run не запускаем — проверяем то, что уже дважды
ломалось молча: значения свойств и экранирование секретов в EnvironmentFile.
"""

from __future__ import annotations

import ipaddress

import pytest

from srv_explore import sandbox


def _props(max_sec=None) -> dict[str, str]:
    return dict(p.split("=", 1) for p in sandbox.props(max_sec))


def test_hardening_properties_present():
    p = _props()
    assert p["ProtectSystem"] == "strict"
    assert p["NoNewPrivileges"] == "yes"
    assert p["IPAddressDeny"] == "any"
    assert int(p["RuntimeMaxSec"]) > 0
    # одиночная команда живёт своим капом, прогон агента — общим
    assert _props("60")["RuntimeMaxSec"] == "60"


def test_ip_allow_are_parseable_prefixes():
    """systemd не понимает именованные токены вроде `localhost` — на этом уже
    падал egress-фильтр целиком (юнит стартовал, IPAddressAllow игнорировался)."""
    for token in sandbox._ip_allow().split():
        ipaddress.ip_network(token, strict=True)


def test_trusted_cidrs_are_appended(monkeypatch):
    monkeypatch.setenv("SRV_EXPLORE_TRUSTED_CIDRS", "203.0.113.0/24")
    allow = sandbox._ip_allow().split()
    assert "203.0.113.0/24" in allow
    assert "127.0.0.0/8" in allow  # база не потерялась


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://u:p@h/db",
        'пароль с "кавычками"',
        "back\\slash",
        "dollar$sign#hash",
        "  пробелы по краям  ",
        "",
    ],
)
def test_quote_roundtrips_through_systemd_syntax(value):
    """EnvironmentFile: значение в кавычках, внутри экранированы \\ и ". Раскавычиваем
    обратно тем же правилом и обязаны получить исходную строку."""
    q = sandbox._quote(value)
    assert q.startswith('"') and q.endswith('"')
    body, i, out = q[1:-1], 0, []
    while i < len(body):
        if body[i] == "\\":
            i += 1
        out.append(body[i])
        i += 1
    assert "".join(out) == value


def test_quote_keeps_value_on_one_line():
    """Перевод строки в значении разорвал бы файл на две записи и подставил агенту
    обрезанный секрет."""
    assert "\n" not in sandbox._quote("a\nb")
