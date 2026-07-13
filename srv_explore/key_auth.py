"""Self-serve токен по SSH-ключу: challenge → подпись → verify по tunnel_keys.

Инженер, чей публичный ключ уже в tunnel_keys (внёс админ), доказывает владение
приватным ключом, подписывая одноразовый nonce (`ssh-keygen -Y sign`), и получает
токен без участия админа. Проверка — `ssh-keygen -Y verify` против allowed_signers,
собранного из tunnel_keys. Просто прислать pubkey нельзя (он публичный), доверять
транспорту нельзя (локальные соседи по loopback) — поэтому подпись.
"""

from __future__ import annotations

import secrets
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from srv_explore import tunnel_keys

NAMESPACE = "srv-explore"
CHALLENGE_TTL = timedelta(minutes=5)

# nonce → срок годности; одноразовые, снимаются при погашении. Живут в процессе:
# перезапуск сервиса сбрасывает выданные challenge — не проблема, инженер берёт новый.
_challenges: dict[str, datetime] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_challenge() -> str:
    nonce = secrets.token_urlsafe(24)
    now = _now()
    _challenges[nonce] = now + CHALLENGE_TTL
    for n, exp in list(_challenges.items()):  # подчистка протухших
        if exp < now:
            _challenges.pop(n, None)
    return nonce


def consume_challenge(nonce: str) -> bool:
    exp = _challenges.pop(nonce, None)
    return exp is not None and exp >= _now()


def _label_for_pubkey(pubkey: str) -> tuple[str, str, str] | None:
    """(label, type, blob) по публичному ключу из tunnel_keys, иначе None."""
    try:
        want_type, want_blob = tunnel_keys.parse_pubkey(pubkey)
    except ValueError:
        return None
    try:
        lines = tunnel_keys.store_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for ln in lines:
        parts = ln.split(maxsplit=2)
        if len(parts) >= 2 and parts[0] == want_type and parts[1] == want_blob:
            label = parts[2] if len(parts) == 3 else ""
            return label, want_type, want_blob
    return None


def verify(pubkey: str, nonce: str, signature: str) -> str | None:
    """Проверить подпись nonce ключом pubkey. Вернуть label юзера или None.

    Не гасит challenge — это делает вызывающий после успеха (одна попытка на nonce).
    """
    found = _label_for_pubkey(pubkey)
    if not found:
        return None
    label, ktype, blob = found
    principal = label or "engineer"
    with tempfile.TemporaryDirectory() as d:
        allowed = Path(d) / "allowed_signers"
        allowed.write_text(f"{principal} {ktype} {blob}\n", encoding="utf-8")
        sig = Path(d) / "nonce.sig"
        sig.write_text(signature, encoding="utf-8")
        try:
            proc = subprocess.run(
                [
                    "ssh-keygen",
                    "-Y",
                    "verify",
                    "-f",
                    str(allowed),
                    "-I",
                    principal,
                    "-n",
                    NAMESPACE,
                    "-s",
                    str(sig),
                ],
                input=nonce.encode(),
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
    return label if proc.returncode == 0 else None
