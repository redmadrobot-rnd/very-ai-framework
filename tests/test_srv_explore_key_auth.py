"""key_auth — challenge/подпись/verify. Гоняет реальный ssh-keygen (skip если нет)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from srv_explore import key_auth, tunnel_keys

pytestmark = pytest.mark.skipif(
    shutil.which("ssh-keygen") is None, reason="ssh-keygen недоступен"
)


@pytest.fixture
def keypair(tmp_path: Path):
    key = tmp_path / "id"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(key), "-N", "", "-q"], check=True
    )
    return key, (key.with_suffix(".pub")).read_text().strip()


def sign(
    key: Path, data: str, tmp_path: Path, namespace: str = key_auth.NAMESPACE
) -> str:
    f = tmp_path / "data"
    f.write_bytes(data.encode())
    subprocess.run(
        ["ssh-keygen", "-Y", "sign", "-f", str(key), "-n", namespace, str(f)],
        check=True,
        capture_output=True,
    )
    return (tmp_path / "data.sig").read_text()


def test_challenge_single_use() -> None:
    n = key_auth.new_challenge()
    assert key_auth.consume_challenge(n) is True
    assert key_auth.consume_challenge(n) is False  # второй раз — нет


def test_verify_ok(keypair, tmp_path, monkeypatch) -> None:
    key, pub = keypair
    store = tmp_path / "tunnel_keys"
    monkeypatch.setenv("SRV_EXPLORE_TUNNEL_KEYS", str(store))
    tunnel_keys.add("alice", pub)
    nonce = key_auth.new_challenge()
    sig = sign(key, nonce, tmp_path)
    assert key_auth.verify(pub, nonce, sig) == "alice"


def test_verify_unregistered_key(keypair, tmp_path, monkeypatch) -> None:
    key, pub = keypair
    monkeypatch.setenv("SRV_EXPLORE_TUNNEL_KEYS", str(tmp_path / "empty"))
    nonce = key_auth.new_challenge()
    sig = sign(key, nonce, tmp_path)
    assert key_auth.verify(pub, nonce, sig) is None  # ключа нет в tunnel_keys


def test_verify_wrong_nonce(keypair, tmp_path, monkeypatch) -> None:
    key, pub = keypair
    store = tmp_path / "tunnel_keys"
    monkeypatch.setenv("SRV_EXPLORE_TUNNEL_KEYS", str(store))
    tunnel_keys.add("bob", pub)
    sig = sign(key, "one-nonce", tmp_path)
    assert key_auth.verify(pub, "other-nonce", sig) is None  # подпись не над тем nonce


def test_verify_wrong_namespace(keypair, tmp_path, monkeypatch) -> None:
    key, pub = keypair
    store = tmp_path / "tunnel_keys"
    monkeypatch.setenv("SRV_EXPLORE_TUNNEL_KEYS", str(store))
    tunnel_keys.add("carol", pub)
    nonce = key_auth.new_challenge()
    sig = sign(key, nonce, tmp_path, namespace="wrong-ns")
    assert key_auth.verify(pub, nonce, sig) is None
