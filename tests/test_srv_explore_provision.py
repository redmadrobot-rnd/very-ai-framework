"""Провизионер: proxy/down по конфигу профиля (docker/apt замоканы)."""

from __future__ import annotations

import pytest

from srv_explore import provision


class _Docker:
    PROXY = {
        "image": "img",
        "port": "127.0.0.1:2375:2375",
        "env": {"POST": "0"},
        "sets": {"DOCKER_HOST": "tcp://127.0.0.1:2375"},
    }


def _mock(monkeypatch, calls):
    monkeypatch.setattr(provision.profile_store, "modules", lambda: {"docker": _Docker})
    monkeypatch.setattr(
        provision.subprocess,
        "run",
        lambda *a, **k: calls.append(a[0]) or type("P", (), {"returncode": 0}),
    )


def test_unknown_profile_raises(monkeypatch):
    monkeypatch.setattr(provision.profile_store, "modules", lambda: {})
    with pytest.raises(KeyError):
        provision.proxy("nope")


def test_proxy_runs_container(monkeypatch):
    calls = []
    _mock(monkeypatch, calls)
    assert provision.proxy("docker") == {"DOCKER_HOST": "tcp://127.0.0.1:2375"}
    assert any("run" in c for c in calls)  # docker run вызван


def test_down_returns_env_keys(monkeypatch):
    calls = []
    _mock(monkeypatch, calls)
    assert provision.down("docker") == ["DOCKER_HOST"]
    assert any("rm" in c for c in calls)  # docker rm -f вызван


# --- БД-профиль (postgres, режим Б) ---------------------------------------------


class _Pg:
    ID = "postgres"
    KIND = "postgres"
    CREDS_ENV = "PG_INSPECTOR_DSN"
    RO_ROLE = "srvx_readonly"
    SETUP = "CREATE ROLE {role} PASSWORD '{pw}'; GRANT ... {db} {role}"
    VERIFY = "CREATE TABLE _srvx_probe (x int)"


def _pg_mods(monkeypatch):
    monkeypatch.setattr(provision.profile_store, "modules", lambda: {"postgres": _Pg})


_ADMIN = "postgresql://admin:pw@h:5432/shop"  # pragma: allowlist secret
_SSL = "postgresql://admin:secret@h:5432/shop?ssl=on"  # pragma: allowlist secret


def test_dsn_with_creds_swaps_only_creds():
    ro = provision._dsn_with_creds(_SSL, "srvx_readonly", "newpw")
    assert ro == _SSL.replace("admin:secret", "srvx_readonly:newpw")  # только creds
    assert "secret" not in ro  # админский пароль не утёк в ro-DSN


def test_create_role_runs_setup_and_returns_ro_dsn(monkeypatch):
    _pg_mods(monkeypatch)
    seen = {}

    def fake(mod, dsn, sql):
        seen.update(dsn=dsn, sql=sql)
        return (0, "", "")

    monkeypatch.setattr(provision, "_run_stmt", fake)
    ro = provision.create_role("postgres", _ADMIN)
    assert seen["dsn"] == _ADMIN  # SETUP под admin-DSN
    assert "srvx_readonly" in seen["sql"]  # роль подставлена
    assert ro.startswith("postgresql://srvx_readonly:") and "@h:5432/shop" in ro


def test_create_role_raises_on_setup_failure(monkeypatch):
    _pg_mods(monkeypatch)
    monkeypatch.setattr(provision, "_run_stmt", lambda *a: (1, "", "permission denied"))
    with pytest.raises(RuntimeError):
        provision.create_role("postgres", _ADMIN)


def test_verify_dsn_denied_is_ok(monkeypatch):
    _pg_mods(monkeypatch)
    monkeypatch.setattr(provision, "_run_stmt", lambda *a: (3, "", "permission denied"))
    assert provision.verify_dsn("postgres", "dsn") == "ok"


def test_verify_dsn_accepted_is_broken(monkeypatch):
    _pg_mods(monkeypatch)
    monkeypatch.setattr(provision, "_run_stmt", lambda *a: (0, "CREATE TABLE", ""))
    assert provision.verify_dsn("postgres", "dsn") == "broken"


def test_verify_dsn_no_connection_is_error(monkeypatch):
    # ненулевой код, но не по правам (база недоступна) — НЕ должен показать 'ok'
    _pg_mods(monkeypatch)
    monkeypatch.setattr(
        provision, "_run_stmt", lambda *a: (2, "", "could not connect to server")
    )
    assert provision.verify_dsn("postgres", "dsn") == "error"


def test_probe_setup_when_enabled_without_creds(monkeypatch):
    _pg_mods(monkeypatch)
    monkeypatch.setattr(provision.profile_store, "provisioned", lambda: {})
    assert provision.probe("postgres")["state"] == "setup"


def test_probe_ok_when_verify_denied(monkeypatch):
    _pg_mods(monkeypatch)
    monkeypatch.setattr(
        provision.profile_store, "provisioned", lambda: {"PG_INSPECTOR_DSN": "dsn"}
    )
    monkeypatch.setattr(provision, "verify_dsn", lambda pid, dsn: "ok")
    assert provision.probe("postgres")["state"] == "ok"


class _NoDriverDB:  # БД-профиль без драйвера (KIND нет)
    ID = "mongo"
    CREDS_ENV = "MONGO_INSPECTOR_DSN"


def test_db_profile_with_driver_is_ready(monkeypatch):
    _pg_mods(monkeypatch)
    assert provision.is_db_profile("postgres") is True
    assert provision.has_driver("postgres") is True
    assert provision.ready("postgres") is True


def test_db_profile_without_driver_not_ready(monkeypatch):
    monkeypatch.setattr(
        provision.profile_store, "modules", lambda: {"mongo": _NoDriverDB}
    )
    assert provision.is_db_profile("mongo") is True
    assert provision.has_driver("mongo") is False
    assert provision.ready("mongo") is False  # включать нельзя — нет драйвера


def test_docker_not_db_and_ready(monkeypatch):
    calls = []
    _mock(monkeypatch, calls)
    assert provision.is_db_profile("docker") is False  # у docker есть PROXY
    assert provision.ready("docker") is True
