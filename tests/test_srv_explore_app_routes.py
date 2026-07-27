"""Границы доступа страниц: оболочка и стили публичны, данные — за токеном, а свой
прогон инженер видит только свой. Пускаем настоящее ASGI-приложение, потому что
проверяем именно маршрутизацию с middleware (порядок Route/Mount легко перепутать).
"""

from __future__ import annotations

import pytest

from srv_explore import mcp_server
from srv_explore.token_store import TokenStore

pytest.importorskip("mcp")
pytest.importorskip("httpx")
from starlette.testclient import TestClient  # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "security_probe", lambda: {})
    monkeypatch.setattr(mcp_server.plugin_store, "STATE", str(tmp_path / "p.json"))
    monkeypatch.setenv("SRV_EXPLORE_PLUGIN_STATE", str(tmp_path / "p.json"))

    async def fake_run_agent(task, steps=None):  # noqa: ARG001 — агента не спавним
        return "факты", steps or []

    monkeypatch.setattr(mcp_server, "run_agent", fake_run_agent)
    monkeypatch.setenv("SRV_EXPLORE_ADMIN_TOKEN", "adm_test")
    store = TokenStore(tmp_path / "tokens.json")
    _, alice = store.issue("alice")
    _, bob = store.issue("bob")
    with TestClient(mcp_server.build_app(store)) as client:
        yield client, alice, bob


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_landing_and_css_are_public(app):
    client, _, _ = app
    page = client.get("/")
    assert page.status_code == 200
    assert "srv-explore" in page.text
    css = client.get("/ui.css")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]


def test_app_api_needs_a_valid_token(app):
    client, _, _ = app
    assert client.get("/app/api/me").status_code == 401
    assert client.get("/app/api/me", headers=_auth("srvx_nope")).status_code == 401
    assert client.get("/app/api/me", headers=_auth("adm_wrong")).status_code == 401


def test_mcp_still_gated(app):
    client, _, _ = app
    assert client.get("/mcp").status_code == 401


def test_engineer_token_does_not_reach_admin_api(app):
    client, alice, _ = app
    for path in (
        "/admin/api/users",
        "/admin/api/runs",
        "/admin/api/plugins",
        "/admin/api/settings",
    ):
        assert client.get(path, headers=_auth(alice)).status_code == 401, path
        assert client.get(path).status_code == 401, path
    # оболочка админки публична: токен вводят уже в неё
    assert client.get("/admin").status_code == 200


def test_settings_roundtrip_and_validation(app, monkeypatch):
    client, _, _ = app
    monkeypatch.setattr(mcp_server.settings, "apply_egress", lambda: "домены: 1")
    d = client.get("/admin/api/settings", headers=_auth("adm_test")).json()
    assert [f["name"] for f in d["fields"]] == ["egress_domains", "egress_cidrs"]

    bad = client.post(
        "/admin/api/settings",
        headers=_auth("adm_test"),
        json={"values": {"egress_cidrs": "не адрес"}},
    )
    assert bad.status_code == 400

    ok = client.post(
        "/admin/api/settings",
        headers=_auth("adm_test"),
        json={"values": {"egress_domains": "grafana.example.com"}},
    )
    assert ok.status_code == 200
    assert ok.json()["values"]["egress_domains"] == "grafana.example.com"


def test_me_reports_own_label_and_plugin_status_without_creds(app):
    client, alice, _ = app
    d = client.get("/app/api/me", headers=_auth(alice)).json()
    assert d["label"] == "alice"
    assert d["role"] == "engineer"
    assert d["runs"] == []
    # статус ресурса, но не то, чем к нему ходят: креды в кабинет не уезжают
    assert {"name", "desc", "installed", "enabled"} == set(d["plugins"][0])


def test_admin_token_opens_the_cabinet_too(app):
    client, alice, _ = app
    client.post("/app/api/ask", headers=_auth(alice), json={"task": "вопрос алисы"})
    d = client.get("/app/api/me", headers=_auth("adm_test")).json()
    assert d["role"] == "admin"
    # админ и так видит все прогоны в /admin — прятать их в кабинете нечего
    assert [r["label"] for r in d["runs"]] == ["alice"]


def test_admin_may_read_any_job(app):
    client, alice, _ = app
    job = client.post("/app/api/ask", headers=_auth(alice), json={"task": "т"})
    job_id = job.json()["job_id"]
    r = client.get(f"/app/api/ask/{job_id}", headers=_auth("adm_test"))
    assert r.status_code == 200
    assert r.json()["label"] == "alice"


def test_engineer_sees_only_his_own_runs(app):
    client, alice, bob = app
    job = client.post("/app/api/ask", headers=_auth(alice), json={"task": "что там"})
    job_id = job.json()["job_id"]
    assert client.get(f"/app/api/ask/{job_id}", headers=_auth(alice)).status_code == 200
    # чужой прогон неотличим от несуществующего
    assert client.get(f"/app/api/ask/{job_id}", headers=_auth(bob)).status_code == 404
    assert client.get(f"/app/api/ask/{job_id}").status_code == 401
    assert client.get("/app/api/me", headers=_auth(bob)).json()["runs"] == []
    assert client.get("/app/api/me", headers=_auth(alice)).json()["runs"][0][
        "task"
    ] == ("что там")


def test_ask_requires_task(app):
    client, alice, _ = app
    assert (
        client.post(
            "/app/api/ask", headers=_auth(alice), json={"task": " "}
        ).status_code
        == 400
    )
