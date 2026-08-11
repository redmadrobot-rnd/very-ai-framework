"""Тесты чистой логики MCP-сервера srv-explore: bearer-авторизация, мост к guard.py,
загрузка системного промпта. SDK/MCP-зависимые части (run_agent/build_app) здесь не
трогаем — они требуют claude-agent-sdk/mcp и живут за ленивым импортом."""

from __future__ import annotations

import asyncio

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


@pytest.fixture(autouse=True)
def _utf8(monkeypatch):
    monkeypatch.setenv("PYTHONUTF8", "1")


# --- воркер агента (гард + промпт) --------------------------------------------


def test_worker_prompt_loads():
    from srv_explore import agent_worker

    prompt = agent_worker._prompt()
    assert prompt
    assert "чтение" in prompt.lower() or "read" in prompt.lower()


# --- таблица допусков (чистая, без ASGI) --------------------------------------


@pytest.mark.parametrize(
    "path,need",
    [
        ("/", None),
        ("/ui.css", None),
        ("/admin", None),
        ("/admin/api/users", "admin"),
        ("/admin/api/plugins", "admin"),
        ("/app/api/me", "engineer"),
        ("/app/api/ask/job_1", "engineer"),
        ("/mcp", "engineer"),
        ("/что-то/новое", "engineer"),  # неизвестный путь закрыт по умолчанию
    ],
)
def test_required_role(path, need):
    assert mcp_server.required_role(path) == need


def test_admin_role_passes_engineer_gates():
    assert mcp_server.role_allows("admin", "engineer")
    assert mcp_server.role_allows("engineer", "engineer")
    assert not mcp_server.role_allows("engineer", "admin")
    assert mcp_server.role_allows("engineer", None)


def test_identify_prefers_admin_then_engineer(tmp_path, monkeypatch):
    monkeypatch.setenv("SRV_EXPLORE_ADMIN_TOKEN", "adm_x")
    store = TokenStore(tmp_path / "t.json")
    _, token = store.issue("alice")
    assert mcp_server.identify("Bearer adm_x", store).role == "admin"
    who = mcp_server.identify(f"Bearer {token}", store)
    assert (who.role, who.label) == ("engineer", "alice")
    assert mcp_server.identify("Bearer nope", store) is None


def test_job_wait_returns_when_run_finishes():
    """Вызов srv_explore держится до конца прогона — на этом ожидании."""

    async def scenario():
        jobs = mcp_server.JobRegistry()

        async def work(steps):
            steps.append({"cmd": "ls", "ok": True, "reason": ""})
            return "факты", steps

        job_id = jobs.start("задача", label="alice", coro_factory=work)
        assert await jobs.wait(job_id, timeout=5) is True
        job = jobs.get(job_id)
        assert (job["status"], job["result"]) == ("done", "факты")

    asyncio.run(scenario())


def test_job_wait_times_out_but_run_survives():
    """Не уложились — отдаём job_id, прогон продолжается и добирается поллингом."""

    async def scenario():
        jobs = mcp_server.JobRegistry()
        started = asyncio.Event()

        async def work(steps):  # noqa: ARG001
            started.set()
            await asyncio.sleep(0.2)
            return "поздние факты", []

        job_id = jobs.start("долгая", label="alice", coro_factory=work)
        await started.wait()
        assert await jobs.wait(job_id, timeout=0.01) is False
        assert jobs.get(job_id)["status"] == "running"
        assert await jobs.wait(job_id, timeout=5) is True
        assert jobs.get(job_id)["result"] == "поздние факты"

    asyncio.run(scenario())


def test_history_survives_restart_without_results(tmp_path):
    """Файл истории переживает «рестарт» (новый реестр): статусы и шаги на месте,
    running честно становится error, а result — только в оперативе, не на диске."""
    path = tmp_path / "jobs.json"
    jobs = mcp_server.JobRegistry(path=path)
    done = jobs.record(
        "df -h",
        label="alice",
        status="done",
        result="данные прода",
        steps=[{"cmd": "df -h", "ok": True, "reason": ""}],
    )
    running = jobs._add("долгая задача", label="bob")
    jobs.save()
    assert "данные прода" not in path.read_text(encoding="utf-8")

    reborn = mcp_server.JobRegistry(path=path)
    survived = reborn.get(done["id"])
    assert (survived["status"], survived["result"]) == ("done", None)
    assert survived["steps"] == [{"cmd": "df -h", "ok": True, "reason": ""}]
    interrupted = reborn.get(running["id"])
    assert interrupted["status"] == "error"
    assert "рестартом" in interrupted["error"]
    assert interrupted["finished"] is not None


def test_recorded_command_lands_in_history():
    jobs = mcp_server.JobRegistry()
    jobs.record("ls /etc", label="alice", status="done", result="passwd")
    (job,) = jobs.recent()
    assert (job["task"], job["status"], job["finished"] is not None) == (
        "ls /etc",
        "done",
        True,
    )
