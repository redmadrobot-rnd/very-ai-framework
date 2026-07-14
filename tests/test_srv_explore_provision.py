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
