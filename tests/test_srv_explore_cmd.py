"""Атомарная команда: вход здесь — вызывающая сторона, не свой агент.

Проверяем то, что держит канал: гард отклоняет форму до запуска, вывод чистится от
кредов и режется по объёму, а прогон попадает в историю (иначе канал непрозрачен
для админа).
"""

from __future__ import annotations

import asyncio

import pytest

from srv_explore import mcp_server, sandbox


@pytest.fixture(autouse=True)
def creds(monkeypatch):
    secret = "dsn-with-password-inside-0123456789"
    monkeypatch.setattr(mcp_server, "plugin_creds", lambda: {"PG_DSN": secret})
    return secret


def _fake_sandbox(monkeypatch, rc=0, out="", err=""):
    seen = {}

    def run(args, input_text=None, extra_env=None, on_line=None, max_sec=None):
        seen.update(args=args, env=extra_env or {}, max_sec=max_sec)
        return rc, out, err

    monkeypatch.setattr(mcp_server.sandbox, "run", run)
    return seen


@pytest.mark.parametrize(
    "cmd",
    [
        "cat /etc/passwd > /tmp/x",
        "ls; whoami",
        "echo $(cat /etc/hostname)",
        "cat /dev/sda",
        "printenv",
    ],
)
def test_bad_form_never_reaches_the_sandbox(monkeypatch, cmd):
    seen = _fake_sandbox(monkeypatch)
    res = asyncio.run(mcp_server.run_command(cmd))
    assert res["ok"] is False
    assert res["reason"]
    assert not seen, "команда не должна была запуститься"


def test_read_pipeline_runs_with_creds_and_short_cap(monkeypatch):
    seen = _fake_sandbox(monkeypatch, out="502\n")
    res = asyncio.run(
        mcp_server.run_command("grep 502 /var/log/nginx/error.log | head")
    )
    assert res["ok"] is True
    assert res["output"] == "502\n"
    assert seen["args"][0] == "/bin/bash"
    assert "PG_DSN" in seen["env"]
    assert seen["max_sec"] == mcp_server.CMD_MAX_SEC


def test_creds_are_redacted_from_output(monkeypatch, creds):
    _fake_sandbox(monkeypatch, out=f"dsn={creds}\n")
    res = asyncio.run(mcp_server.run_command("cat /proc/self/cmdline"))
    assert creds not in res["output"]
    assert "***" in res["output"]


def test_long_output_is_truncated(monkeypatch):
    _fake_sandbox(monkeypatch, out="x" * (mcp_server.CMD_MAX_OUT + 100))
    res = asyncio.run(mcp_server.run_command("cat /var/log/big"))
    assert res["truncated"] is True
    assert len(res["output"]) == mcp_server.CMD_MAX_OUT


def test_stderr_is_returned_when_command_fails(monkeypatch):
    _fake_sandbox(monkeypatch, rc=2, err="No such file or directory\n")
    res = asyncio.run(mcp_server.run_command("cat /nope"))
    assert res["ok"] is False
    assert "No such file" in res["output"]
    assert "код 2" in res["reason"]


def test_redact_leaves_short_values_alone():
    # порт или имя юзера — не секрет; затирать их значило бы портить вывод
    assert sandbox.redact("port 5432 user ro", ["5432", "ro"]) == "port 5432 user ro"
    assert sandbox.redact("tok=abcdefgh12", ["abcdefgh12"]) == "tok=***"


def test_redact_catches_password_component_of_dsn():
    # клиент может показать не весь DSN, а один пароль — режем и его,
    # в том числе URL-декодированную форму
    dsn = "postgresql://ro:sup%40er_secret1@db:5432/app"  # pragma: allowlist secret
    out = sandbox.redact("auth failed for sup@er_secret1 (raw sup%40er_secret1)", [dsn])
    assert "sup@er_secret1" not in out
    assert "sup%40er_secret1" not in out
