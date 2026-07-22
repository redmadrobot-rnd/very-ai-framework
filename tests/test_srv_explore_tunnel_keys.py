"""tunnel_keys — валидация ключей и файл для AuthorizedKeysCommand."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from srv_explore import tunnel_keys

KEY = "ssh-ed25519 " + base64.b64encode(b"k" * 32).decode()


@pytest.fixture(autouse=True)
def store(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "tunnel_keys"
    monkeypatch.setenv("SRV_EXPLORE_TUNNEL_KEYS", str(path))
    return path


def test_add_and_remove(store: Path) -> None:
    tunnel_keys.add("alice", KEY + " laptop")
    assert store.read_text(encoding="utf-8").strip() == f"{KEY} alice"
    assert tunnel_keys.remove_label("alice") == 1
    assert store.read_text(encoding="utf-8").strip() == ""


def test_same_key_replaced(store: Path) -> None:
    tunnel_keys.add("alice", KEY)
    tunnel_keys.add("bob", KEY)
    lines = store.read_text(encoding="utf-8").splitlines()
    assert lines == [f"{KEY} bob"]


def test_remove_missing_label() -> None:
    assert tunnel_keys.remove_label("nobody") == 0


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "ssh-ed25519",
        "ssh-dss AAAA",
        "ssh-ed25519 не-base64!",
        "ssh-ed25519 QUFBQQ==\nssh-rsa QUFBQQ==",
        'command="rm -rf /" ssh-ed25519 QUFBQQ==',
    ],
)
def test_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError):
        tunnel_keys.add("x", bad)
