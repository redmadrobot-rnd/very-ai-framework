"""Ручки админки: разбор списков, валидация и сборка allowlist прокси.

Проверяем главное свойство — деплойный уровень (env) и админский (settings.json)
складываются, а не затирают друг друга, и мусор до диска не доходит.
"""

from __future__ import annotations

import pytest

from srv_explore import settings


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SRV_EXPLORE_PLUGIN_STATE", str(tmp_path / "plugins.json"))
    monkeypatch.delenv("SRV_EXPLORE_TRUSTED_DOMAINS", raising=False)
    monkeypatch.delenv("SRV_EXPLORE_TRUSTED_CIDRS", raising=False)
    return tmp_path


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("a, b", ["a", "b"]),
        ("a b\nc", ["a", "b", "c"]),
        ("a, a, b", ["a", "b"]),
        ("", []),
        ("   ", []),
    ],
)
def test_split_list(raw, want):
    assert settings.split_list(raw) == want


def test_parse_domains_normalizes():
    doms, err = settings.parse_domains("Grafana.Example.COM., s3.internal")
    assert err == ""
    assert doms == ["grafana.example.com", "s3.internal"]


@pytest.mark.parametrize("bad", ["not_a_domain", "http://x.com", "-x.com", "x..com"])
def test_parse_domains_rejects(bad):
    doms, err = settings.parse_domains(bad)
    assert doms == [] and err


def test_parse_cidrs_accepts_host_and_net():
    nets, err = settings.parse_cidrs("203.0.113.10, 198.51.100.7/24")
    assert err == ""
    assert nets == ["203.0.113.10/32", "198.51.100.0/24"]


def test_parse_cidrs_rejects_garbage():
    nets, err = settings.parse_cidrs("203.0.113.999")
    assert nets == [] and err


def test_parse_cidrs_rejects_whole_internet():
    # /0 — это снятие egress-firewall, а не подсеть; опечатка не должна такое уметь
    for raw in ("0.0.0.0/0", "::/0", "203.0.113.10, 0.0.0.0/0"):
        nets, err = settings.parse_cidrs(raw)
        assert nets == [] and "весь интернет" in err


def test_validate_reports_first_error():
    assert settings.validate({"egress_domains": "ok.example.com"}) == ""
    assert settings.validate({"egress_cidrs": "nope"})


def test_save_load_roundtrip_keeps_only_declared_fields():
    saved = settings.save({"egress_domains": " a.example.com ", "junk": "x"})
    assert saved["egress_domains"] == "a.example.com"
    assert "junk" not in saved
    assert settings.load()["egress_domains"] == "a.example.com"


def test_effective_values_are_env_plus_settings(monkeypatch):
    monkeypatch.setenv("SRV_EXPLORE_TRUSTED_DOMAINS", "from-env.example.com")
    monkeypatch.setenv("SRV_EXPLORE_TRUSTED_CIDRS", "10.1.0.0/16")
    settings.save({"egress_domains": "from-ui.example.com", "egress_cidrs": "10.2.0.3"})
    assert settings.egress_domains() == [
        "from-env.example.com",
        "from-ui.example.com",
    ]
    assert settings.egress_cidrs() == ["10.1.0.0/16", "10.2.0.3/32"]


def test_allow_file_text_always_has_model_domain_once():
    text = settings.allow_file_text([settings.MODEL_DOMAIN, "x.example.com"])
    lines = text.splitlines()
    assert len(lines) == 2
    assert lines[0] == rf"(^|\.){settings.MODEL_DOMAIN.replace('.', chr(92) + '.')}$"


def test_apply_egress_merges_base_and_settings(tmp_path, monkeypatch):
    base = tmp_path / "proxy-allow.base"
    base.write_text("deploy.example.com\n", encoding="utf-8")
    out = tmp_path / "proxy-allow"
    monkeypatch.setattr(settings, "PROXY_ALLOW_BASE", base)
    monkeypatch.setattr(settings, "PROXY_ALLOW", out)
    monkeypatch.setattr(settings.subprocess, "run", _fake_run(0))
    settings.save({"egress_domains": "ui.example.com"})
    report = settings.apply_egress()
    text = out.read_text(encoding="utf-8")
    assert "deploy" in text and "ui" in text and "anthropic" in text
    assert "домены: 3" in report


def test_apply_egress_reports_failed_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PROXY_ALLOW_BASE", tmp_path / "nope")
    monkeypatch.setattr(settings, "PROXY_ALLOW", tmp_path / "proxy-allow")
    monkeypatch.setattr(settings.subprocess, "run", _fake_run(1, "unit not found"))
    assert "не перезапущен" in settings.apply_egress()


def _fake_run(code: int, stderr: str = ""):
    class R:
        returncode = code

    R.stderr = stderr

    def run(*a, **kw):  # noqa: ARG001 — systemctl в тестах не зовём
        return R

    return run
