"""Тесты истории прогонов srv-explore (монитор задач + лог сессий в /admin)."""

from __future__ import annotations

import pytest

from srv_explore.run_store import RESULT_MAX, RunStore


@pytest.fixture
def runs_path(tmp_path):
    return tmp_path / "runs.json"


def test_start_marks_running(runs_path):
    store = RunStore(runs_path)
    rec = store.start("job_1", task="что с памятью?", label="alice", env="dev")
    assert rec.status == "running"
    assert rec.finished is None
    got = store.get("job_1")
    assert got.task == "что с памятью?"
    assert got.label == "alice"


def test_finish_done_and_error(runs_path):
    store = RunStore(runs_path)
    store.start("job_ok", task="t", label="a", env="dev")
    store.finish("job_ok", result="findings…")
    assert store.get("job_ok").status == "done"
    assert store.get("job_ok").result == "findings…"

    store.start("job_bad", task="t", label="a", env="dev")
    store.finish("job_bad", error="boom")
    assert store.get("job_bad").status == "error"
    assert store.get("job_bad").error == "boom"


def test_finish_unknown_is_noop(runs_path):
    store = RunStore(runs_path)
    store.finish("nope", result="x")  # не должно падать
    assert store.get("nope") is None


def test_persistence_across_reload(runs_path):
    store = RunStore(runs_path)
    store.start("job_1", task="t1", label="a", env="dev")
    store.finish("job_1", result="r1")

    reloaded = RunStore(runs_path)
    rec = reloaded.get("job_1")
    assert rec is not None
    assert rec.status == "done"
    assert rec.result == "r1"


def test_list_recent_newest_first_and_limit(runs_path):
    store = RunStore(runs_path)
    for i in range(5):
        store.start(f"job_{i}", task=f"t{i}", label="a", env="dev")
    recent = store.list_recent(limit=3)
    assert len(recent) == 3
    # started монотонно возрастает по порядку вставки → новейший первым
    assert recent[0].started >= recent[-1].started


def test_corrupt_file_does_not_crash(runs_path):
    runs_path.write_text("{not json}\n", encoding="utf-8")
    store = RunStore(runs_path)  # не должно бросить
    assert store.list_recent() == []


def test_keeps_only_last_n_per_user(runs_path, monkeypatch):
    monkeypatch.setenv("SRV_EXPLORE_HISTORY_PER_USER", "3")
    store = RunStore(runs_path)
    for i in range(5):
        store.start(f"a{i}", task=f"t{i}", label="alice", env="dev")
    alice = [r for r in store.list_recent() if r.label == "alice"]
    assert len(alice) == 3
    # выбывшие (старейшие) не находятся по id
    assert store.get("a0") is None and store.get("a1") is None
    assert store.get("a4") is not None


def test_cap_is_per_user_independent(runs_path, monkeypatch):
    monkeypatch.setenv("SRV_EXPLORE_HISTORY_PER_USER", "3")
    store = RunStore(runs_path)
    for i in range(5):
        store.start(f"a{i}", task="t", label="alice", env="dev")
    for i in range(2):
        store.start(f"b{i}", task="t", label="bob", env="dev")
    runs = store.list_recent(100)
    assert len([r for r in runs if r.label == "alice"]) == 3
    assert len([r for r in runs if r.label == "bob"]) == 2


def test_cap_default_is_15(runs_path, monkeypatch):
    monkeypatch.delenv("SRV_EXPLORE_HISTORY_PER_USER", raising=False)
    store = RunStore(runs_path)
    assert store.cap == 15


def test_result_is_clipped(runs_path):
    store = RunStore(runs_path)
    store.start("j", task="t", label="a", env="dev")
    store.finish("j", result="x" * (RESULT_MAX + 500))
    out = store.get("j").result
    assert len(out) < RESULT_MAX + 100
    assert out.endswith("[обрезано]")
