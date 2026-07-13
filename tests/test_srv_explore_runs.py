"""Тесты истории прогонов: последние N на юзера, персист по завершении."""

from __future__ import annotations

import pytest

from srv_explore.run_store import RESULT_MAX, RunRecord, RunStore


@pytest.fixture
def runs_path(tmp_path):
    return tmp_path / "runs.json"


def _rec(rid, label="alice", status="done", started="2026-07-01T00:00:00+00:00"):
    return RunRecord(id=rid, task="t", label=label, status=status, started=started)


def test_add_and_list(runs_path):
    store = RunStore(runs_path)
    store.add(_rec("j1"))
    got = store.list_recent()
    assert len(got) == 1
    assert got[0].id == "j1"


def test_keeps_only_last_n_per_user(runs_path, monkeypatch):
    monkeypatch.setenv("SRV_EXPLORE_HISTORY_PER_USER", "3")
    store = RunStore(runs_path)
    for i in range(5):
        store.add(_rec(f"a{i}", started=f"2026-07-01T00:00:0{i}+00:00"))
    alice = [r for r in store.list_recent() if r.label == "alice"]
    assert len(alice) == 3
    ids = {r.id for r in alice}
    assert "a0" not in ids and "a1" not in ids and "a4" in ids


def test_cap_is_per_user_independent(runs_path, monkeypatch):
    monkeypatch.setenv("SRV_EXPLORE_HISTORY_PER_USER", "3")
    store = RunStore(runs_path)
    for i in range(5):
        store.add(_rec(f"a{i}", label="alice"))
    for i in range(2):
        store.add(_rec(f"b{i}", label="bob"))
    runs = store.list_recent(100)
    assert len([r for r in runs if r.label == "alice"]) == 3
    assert len([r for r in runs if r.label == "bob"]) == 2


def test_cap_default_is_15(runs_path, monkeypatch):
    monkeypatch.delenv("SRV_EXPLORE_HISTORY_PER_USER", raising=False)
    assert RunStore(runs_path).cap == 15


def test_result_is_clipped(runs_path):
    store = RunStore(runs_path)
    rec = _rec("j")
    rec.result = "x" * (RESULT_MAX + 500)
    store.add(rec)
    out = store.list_recent()[0].result
    assert len(out) < RESULT_MAX + 100
    assert out.endswith("[обрезано]")


def test_persistence_across_reload(runs_path):
    store = RunStore(runs_path)
    store.add(_rec("j1", label="alice"))
    reloaded = RunStore(runs_path)
    got = reloaded.list_recent()
    assert len(got) == 1 and got[0].label == "alice"


def test_list_recent_newest_first(runs_path):
    store = RunStore(runs_path)
    store.add(_rec("old", started="2026-07-01T00:00:00+00:00"))
    store.add(_rec("new", started="2026-07-02T00:00:00+00:00"))
    assert store.list_recent()[0].id == "new"


def test_steps_persisted(runs_path):
    store = RunStore(runs_path)
    rec = _rec("j")
    rec.steps = [
        {"cmd": "df -h", "ok": True, "reason": ""},
        {"cmd": "rm x", "ok": False, "reason": "blocked"},
    ]
    store.add(rec)
    got = RunStore(runs_path).list_recent()[0]
    assert len(got.steps) == 2
    assert got.steps[0]["cmd"] == "df -h"
    assert got.steps[1]["ok"] is False


def test_old_record_without_steps_loads(runs_path):
    runs_path.write_text(
        '{"alice":[{"id":"j","task":"t","label":"alice",'
        '"status":"done","started":"2026-07-01T00:00:00+00:00"}]}',
        encoding="utf-8",
    )
    got = RunStore(runs_path).list_recent()[0]
    assert got.steps == []


def test_corrupt_file_does_not_crash(runs_path):
    runs_path.write_text("{not json}\n", encoding="utf-8")
    assert RunStore(runs_path).list_recent() == []
