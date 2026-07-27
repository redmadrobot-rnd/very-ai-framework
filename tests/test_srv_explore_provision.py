"""Раннер установки плагина: чеклист, остановка на первом ✗, выдача кред."""

from __future__ import annotations

import pytest

from srv_explore import plugin_api, profile_store, provision
from srv_explore.plugin_api import step


class _Good:
    ID = "good"
    DESC = "тестовый плагин"
    NEEDS_ADMIN_DSN = False

    @staticmethod
    def install(ctx):
        yield step("клиент", True, "есть")
        yield step("ресурс", True, "отвечает")
        ctx.creds = {"GOOD_DSN": "x://ro"}


class _Failing:
    ID = "bad"
    DESC = "падает на втором шаге"
    NEEDS_ADMIN_DSN = True

    @staticmethod
    def install(ctx):
        yield step("клиент", True, "есть")
        yield step("ресурс", False, "недоступен")
        yield step("не должен выполниться", True)
        ctx.creds = {"BAD_DSN": "x://ro"}


class _Raising:
    ID = "boom"
    DESC = "кидает исключение"

    @staticmethod
    def install(ctx):
        yield step("клиент", True)
        raise RuntimeError("клиент сдох")


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    """Изолированный StateDir + подменённый реестр плагинов."""
    monkeypatch.setattr(profile_store, "STATE", str(tmp_path / "profiles.json"))
    monkeypatch.setattr(
        profile_store,
        "modules",
        lambda: {"good": _Good, "bad": _Failing, "boom": _Raising},
    )


def test_install_ok_saves_creds():
    res = provision.install("good")
    assert res["ok"] is True
    assert [s["name"] for s in res["checklist"]] == ["клиент", "ресурс"]
    assert profile_store.is_installed("good")
    assert profile_store.creds_all()["good"] == {"GOOD_DSN": "x://ro"}


def test_install_stops_at_first_failure():
    res = provision.install("bad", "postgresql://a:b@h/db")  # pragma: allowlist secret
    assert res["ok"] is False
    names = [s["name"] for s in res["checklist"]]
    assert names == ["клиент", "ресурс"]  # третий шаг не выполнялся
    assert not profile_store.is_installed("bad")
    assert "bad" not in profile_store.creds_all()  # креды не сохранены


def test_install_catches_plugin_exception():
    res = provision.install("boom")
    assert res["ok"] is False
    assert res["checklist"][-1]["name"] == "установка прервана"
    assert "клиент сдох" in res["checklist"][-1]["detail"]


def test_needs_admin_dsn():
    assert provision.needs_admin_dsn("bad") is True
    assert provision.needs_admin_dsn("good") is False


def test_unknown_plugin_raises():
    with pytest.raises(KeyError):
        provision.install("nope")


def test_active_creds_only_installed_and_enabled():
    provision.install("good")
    assert profile_store.active_creds() == {}  # установлен, но выключен
    profile_store.set_enabled("good", True)
    assert profile_store.active_creds() == {"GOOD_DSN": "x://ro"}
    profile_store.set_enabled("good", False)
    assert profile_store.active_creds() == {}


def test_uninstall_clears_state():
    provision.install("good")
    profile_store.set_enabled("good", True)
    provision.uninstall("good")
    assert not profile_store.is_installed("good")
    assert profile_store.active_creds() == {}
    assert profile_store.load()["good"] is False


# --- DSN-хелперы контракта ------------------------------------------------------

_SSL = "postgresql://admin:secret@h:5432/shop?ssl=on"  # pragma: allowlist secret


def test_dsn_with_creds_swaps_only_creds():
    ro = plugin_api.dsn_with_creds(_SSL, "srvx_readonly", "newpw")
    assert ro == _SSL.replace("admin:secret", "srvx_readonly:newpw")
    assert "secret" not in ro  # админский пароль не утёк


def test_dsn_parts():
    assert plugin_api.dsn_host(_SSL) == ("h", 5432)
    assert plugin_api.dsn_db(_SSL) == "shop"
    assert plugin_api.dsn_user(_SSL) == ("admin", "secret")
