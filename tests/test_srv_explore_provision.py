"""Раннер установки плагина: чеклист, остановка на первом ✗, выдача кред."""

from __future__ import annotations

import pytest

from srv_explore import plugin_api, profile_store, provision
from srv_explore.plugin_api import field, step

_ADMIN = "postgresql://admin:s3cr3t@h:5432/shop"  # pragma: allowlist secret
_SSL = "postgresql://admin:secret@h:5432/shop?ssl=on"  # pragma: allowlist secret


class _Good:
    ID = "good"
    DESC = "тестовый плагин"
    FIELDS = []

    @staticmethod
    def install(ctx):
        yield step("клиент", True, "есть")
        yield step("ресурс", True, "отвечает")
        ctx.creds = {"GOOD_DSN": "x://ro"}


class _Failing:
    ID = "bad"
    DESC = "падает на втором шаге"
    FIELDS = [field("admin_dsn", "Админский DSN", secret=True)]

    @staticmethod
    def install(ctx):
        yield step("клиент", True, "есть")
        yield step("ресурс", False, f"недоступен: {ctx.field('admin_dsn')}")
        yield step("не должен выполниться", True)
        ctx.creds = {"BAD_DSN": "x://ro"}


class _Leaky:
    """Плагин, который тащит секреты в детали шагов — ядро обязано их вырезать."""

    ID = "leaky"
    DESC = "светит секреты"
    FIELDS = [field("admin_dsn", "Админский DSN", secret=True)]

    @staticmethod
    def install(ctx):
        pw = ctx.password()
        yield step("эхо", True, f"dsn={ctx.field('admin_dsn')} pw={pw}")
        ctx.creds = {"LEAKY_DSN": "x://ro"}


class _Raising:
    ID = "boom"
    DESC = "кидает исключение"

    @staticmethod
    def install(ctx):
        yield step("клиент", True)
        raise RuntimeError("клиент сдох")


class _Mute:
    """Успешно проходит чеклист, но кред не выдаёт."""

    ID = "mute"
    DESC = "без кред"

    @staticmethod
    def install(ctx):
        yield step("клиент", True)


class _Empty:
    ID = "empty"
    DESC = "не выдаёт ни шага"

    @staticmethod
    def install(ctx):
        return
        yield  # pragma: no cover — делает функцию генератором


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    """Изолированный StateDir + подменённый реестр плагинов."""
    monkeypatch.setattr(profile_store, "STATE", str(tmp_path / "profiles.json"))
    monkeypatch.setattr(
        profile_store,
        "modules",
        lambda: {
            "good": _Good,
            "bad": _Failing,
            "boom": _Raising,
            "leaky": _Leaky,
            "mute": _Mute,
            "empty": _Empty,
        },
    )


def test_install_ok_saves_creds():
    res = provision.install("good")
    assert res["ok"] is True
    assert [s["name"] for s in res["checklist"]] == ["клиент", "ресурс"]
    assert profile_store.is_installed("good")
    assert profile_store.creds_all()["good"] == {"GOOD_DSN": "x://ro"}


def test_install_stops_at_first_failure():
    res = provision.install("bad", {"admin_dsn": _ADMIN})
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


def test_fields_come_from_plugin():
    assert provision.fields("good") == []
    assert [f["name"] for f in provision.fields("bad")] == ["admin_dsn"]


def test_missing_required_fields():
    assert provision.missing_fields("bad", {}) == ["admin_dsn"]
    assert provision.missing_fields("bad", {"admin_dsn": "   "}) == ["admin_dsn"]
    assert provision.missing_fields("bad", {"admin_dsn": "x://y"}) == []
    assert provision.missing_fields("good", {}) == []


def test_secrets_never_reach_stored_checklist():
    res = provision.install("leaky", {"admin_dsn": _ADMIN})
    detail = res["checklist"][0]["detail"]
    assert _ADMIN not in detail  # введённый секрет вырезан
    assert "***" in detail
    stored = profile_store.installed_all()["leaky"]["checklist"][0]["detail"]
    assert _ADMIN not in stored  # и на диск он тоже не попал
    assert "pw=***" in stored  # сгенерированный пароль тоже


def test_plugin_without_steps_is_not_installed():
    res = provision.install("empty")
    assert res["ok"] is False
    assert not profile_store.is_installed("empty")


def test_reinstall_without_creds_drops_previous_ones(monkeypatch):
    """Плагин выдал креды, потом переустановка прошла молча — старые креды должны
    уйти, иначе агент получит доступ, которого свежая установка не подтверждала."""
    provision.install("good")
    profile_store.set_enabled("good", True)
    assert profile_store.active_creds() == {"GOOD_DSN": "x://ro"}

    monkeypatch.setattr(_Good, "install", _Mute.install)
    res = provision.install("good")
    assert res["ok"] is True
    assert profile_store.creds_all() == {}
    assert profile_store.active_creds() == {}


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


def test_dsn_with_creds_swaps_only_creds():
    ro = plugin_api.dsn_with_creds(_SSL, "srvx_readonly", "newpw")
    assert ro == _SSL.replace("admin:secret", "srvx_readonly:newpw")
    assert "secret" not in ro  # админский пароль не утёк


def test_dsn_parts():
    assert plugin_api.dsn_host(_SSL) == ("h", 5432)
    assert plugin_api.dsn_db(_SSL) == "shop"
    assert plugin_api.dsn_user(_SSL) == ("admin", "secret")
