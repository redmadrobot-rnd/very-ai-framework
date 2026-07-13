"""Публичные SSH-ключи туннельных юзеров: tunnel_keys в StateDir.

sshd читает файл через AuthorizedKeysCommand (Match User srvx-tunnel, см. install.sh);
ограничения (только проброс 8765, без shell/tty) навешивает sshd-конфиг, поэтому
строки хранятся чистыми: "<type> <base64> <label>".
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

DEFAULT_STORE = "/var/lib/srv-explore/tunnel_keys"

KEY_TYPES = {
    "ssh-ed25519",
    "ssh-rsa",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
}


def store_path() -> Path:
    return Path(os.environ.get("SRV_EXPLORE_TUNNEL_KEYS", DEFAULT_STORE))


def parse_pubkey(pubkey: str) -> tuple[str, str]:
    """(type, base64) из строки публичного ключа; ValueError если не ключ."""
    line = pubkey.strip()
    if "\n" in line or "\r" in line:
        raise ValueError("ключ должен быть одной строкой")
    parts = line.split()
    if len(parts) < 2:
        raise ValueError("ожидается '<type> <base64> [comment]'")
    ktype, blob = parts[0], parts[1]
    if ktype not in KEY_TYPES:
        raise ValueError(f"неизвестный тип ключа {ktype!r}")
    try:
        base64.b64decode(blob, validate=True)
    except Exception as e:
        raise ValueError("тело ключа — не base64") from e
    return ktype, blob


def _lines() -> list[str]:
    try:
        text = store_path().read_text(encoding="utf-8")
    except OSError:
        return []
    return [ln for ln in text.splitlines() if ln.strip()]


def _save(lines: list[str]) -> None:
    path = store_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(ln + "\n" for ln in lines), encoding="utf-8")
    os.replace(tmp, path)


def add(label: str, pubkey: str) -> None:
    ktype, blob = parse_pubkey(pubkey)
    lines = [ln for ln in _lines() if ln.split()[1:2] != [blob]]
    lines.append(f"{ktype} {blob} {label}")
    _save(lines)


def remove_label(label: str) -> int:
    lines = _lines()
    kept = [ln for ln in lines if ln.split(maxsplit=2)[2:] != [label]]
    _save(kept)
    return len(lines) - len(kept)


def list_users() -> list[dict]:
    """[{label, type, fp}] — fp = хвост base64 ключа, чтобы отличать глазами."""
    users = []
    for ln in _lines():
        parts = ln.split(maxsplit=2)
        if len(parts) < 2:
            continue
        ktype, blob = parts[0], parts[1]
        label = parts[2] if len(parts) == 3 else ""
        users.append({"label": label, "type": ktype, "fp": "…" + blob[-12:]})
    return users
