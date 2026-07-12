"""Тесты токен-хранилища srv-explore: выдача/отзыв/проверка, хранение только хэшей."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from srv_explore.token_store import (
    TOKEN_PREFIX,
    TokenStore,
    generate_token,
    token_hash,
)


@pytest.fixture
def store_path(tmp_path):
    return tmp_path / "tokens.json"


def test_generate_token_shape_and_uniqueness():
    a = generate_token()
    b = generate_token()
    assert a.startswith(TOKEN_PREFIX)
    assert a != b
    assert len(a) > len(TOKEN_PREFIX) + 20


def test_issue_returns_plaintext_but_stores_only_hash(store_path):
    store = TokenStore(store_path)
    record, token = store.issue("alice", "dev")

    assert token.startswith(TOKEN_PREFIX)
    assert record.env == "dev"
    assert record.label == "alice"

    raw = json.loads(store_path.read_text(encoding="utf-8"))
    assert len(raw) == 1
    # На диске — только хэш, самого токена нет нигде.
    assert raw[0]["sha256"] == token_hash(token)
    assert token not in store_path.read_text(encoding="utf-8")


def test_verify_accepts_valid_rejects_unknown(store_path):
    store = TokenStore(store_path)
    _, token = store.issue("alice", "dev")

    assert store.verify(token) is not None
    assert store.verify(token + "x") is None
    assert store.verify("srvx_nope") is None
    assert store.verify("") is None


def test_verify_env_scoping(store_path):
    store = TokenStore(store_path)
    _, dev_token = store.issue("alice", "dev")

    assert store.verify(dev_token, env="dev") is not None
    # dev-токен не должен пройти в prod.
    assert store.verify(dev_token, env="prod") is None


def test_revoke_removes_token(store_path):
    store = TokenStore(store_path)
    record, token = store.issue("alice", "dev")

    assert store.verify(token) is not None
    assert store.revoke(record.id) is True
    assert store.verify(token) is None
    assert store.revoke(record.id) is False  # повторный отзыв — no-op


def test_persistence_across_reload(store_path):
    store = TokenStore(store_path)
    _, token = store.issue("alice", "prod")

    reloaded = TokenStore(store_path)
    rec = reloaded.verify(token, env="prod")
    assert rec is not None
    assert rec.label == "alice"


def test_verify_reloads_external_changes(store_path):
    # Админ-UI и раннер — разные объекты TokenStore на один файл. verify() перечитывает
    # файл, чтобы выдача/отзыв через UI действовали без рестарта сервиса.
    runner = TokenStore(store_path)
    admin = TokenStore(store_path)

    _, token = admin.issue("alice", "dev")
    # runner создан ДО выдачи — но verify перечитает файл и увидит токен
    assert runner.verify(token, env="dev") is not None

    rec = admin.list()[0]
    admin.revoke(rec.id)
    assert runner.verify(token, env="dev") is None


def test_issue_rejects_bad_env(store_path):
    store = TokenStore(store_path)
    with pytest.raises(ValueError):
        store.issue("alice", "staging")


def test_missing_store_file_is_empty(store_path):
    store = TokenStore(store_path)
    assert store.list() == []
    assert store.verify("anything") is None


def test_cli_issue_list_revoke_roundtrip(store_path):
    def run(*args):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "srv_explore.token_store",
                "--store",
                str(store_path),
                *args,
            ],
            capture_output=True,
            text=True,
        )

    issued = run("issue", "--label", "bob", "--env", "dev")
    assert issued.returncode == 0
    token_line = issued.stdout.strip().splitlines()[-1]
    assert token_line.startswith(TOKEN_PREFIX)

    listed = run("list")
    assert listed.returncode == 0
    assert "bob" in listed.stdout

    # id — первое поле строки issue (id=...)
    token_id = issued.stdout.splitlines()[0].split()[0].split("=")[1]
    revoked = run("revoke", token_id)
    assert revoked.returncode == 0

    assert TokenStore(store_path).verify(token_line) is None
