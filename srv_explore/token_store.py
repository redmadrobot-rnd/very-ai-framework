"""Хранилище bearer-токенов доступа к srv-explore MCP (админ выдаёт/отзывает).

Токен привязан к инстансу: его `sha256` лежит только в этом файле, на другом сервере
не пройдёт. На сервере хранится только хэш — утечка файла не раскрывает сами токены.

CLI: `python -m srv_explore.token_store issue --label alice` / `revoke <id>` / `list`.
Файл хранилища — env `SRV_EXPLORE_TOKENS` или `/var/lib/srv-explore/tokens.json`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

TOKEN_PREFIX = "srvx_"
DEFAULT_STORE = "/var/lib/srv-explore/tokens.json"


def generate_token() -> str:
    """Новый высокоэнтропийный токен для выдачи инженеру."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """sha256 токена в hex — то, что хранится на сервере."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class TokenRecord:
    id: str
    label: str
    sha256: str
    created: str


class TokenStore:
    """JSON-файл со списком выданных токенов (хранятся хэши, не токены)."""

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path or os.environ.get("SRV_EXPLORE_TOKENS", DEFAULT_STORE))
        self._records: list[TokenRecord] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._records = []
            return
        raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        self._records = [TokenRecord(**r) for r in raw]

    def reload(self) -> None:
        """Перечитать файл, не падая на битом содержимом (для рантайма сервиса)."""
        try:
            self._load()
        except (OSError, ValueError, TypeError):
            pass

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(
            [asdict(r) for r in self._records], ensure_ascii=False, indent=2
        )
        # Файл с хэшами токенов (не сами токены). Один владелец — сервис-юзер
        # (StateDirectory): и админ-UI, и CLI (от sudo -u srv-explore) пишут им же.
        self.path.write_text(data + "\n", encoding="utf-8")
        try:
            self.path.chmod(0o640)
        except OSError:
            pass

    def issue(self, label: str, now: datetime | None = None) -> tuple[TokenRecord, str]:
        """Сгенерировать токен, сохранить его хэш, вернуть (запись, токен-в-открытую).

        Открытый токен возвращается ЕДИНОЖДЫ — сохранить его негде, только у выдавшего.
        """
        token = generate_token()
        created = (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
        record = TokenRecord(
            id=secrets.token_hex(4),
            label=label,
            sha256=token_hash(token),
            created=created,
        )
        self._records.append(record)
        self._save()
        return record, token

    def revoke(self, token_id: str) -> bool:
        """Убрать токен по id. True — если что-то удалили."""
        before = len(self._records)
        self._records = [r for r in self._records if r.id != token_id]
        if len(self._records) != before:
            self._save()
            return True
        return False

    def revoke_label(self, label: str) -> int:
        """Убрать все токены с этой меткой (снятие пользователя). Вернуть счётчик."""
        before = len(self._records)
        self._records = [r for r in self._records if r.label != label]
        removed = before - len(self._records)
        if removed:
            self._save()
        return removed

    def list(self) -> list[TokenRecord]:
        return list(self._records)

    def verify(self, token: str) -> TokenRecord | None:
        """Вернуть запись, если токен валиден (сравнение по хэшу), иначе None."""
        if not token:
            return None
        self.reload()  # выдача/отзыв через админ-UI видны без рестарта сервиса
        digest = token_hash(token)
        for r in self._records:
            if secrets.compare_digest(r.sha256, digest):
                return r
        return None


def _cmd_issue(args: argparse.Namespace) -> int:
    store = TokenStore(args.store)
    record, token = store.issue(args.label)
    print(f"id={record.id} label={record.label} created={record.created}")
    print(token)
    print(
        "^ выдай этот токен инженеру ОДИН раз — на сервере он не хранится.",
        file=sys.stderr,
    )
    return 0


def _cmd_revoke(args: argparse.Namespace) -> int:
    store = TokenStore(args.store)
    ok = store.revoke(args.id)
    if ok:
        print(f"revoked {args.id}")
        return 0
    print(f"id {args.id} not found", file=sys.stderr)
    return 1


def _cmd_list(args: argparse.Namespace) -> int:
    store = TokenStore(args.store)
    records = store.list()
    if not records:
        print("(нет выданных токенов)")
        return 0
    for r in records:
        print(f"{r.id}\t{r.created}\t{r.label}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="srv-explore-token", description=__doc__)
    parser.add_argument(
        "--store", default=None, help=f"путь к хранилищу (по умолчанию {DEFAULT_STORE})"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_issue = sub.add_parser("issue", help="сгенерировать и выдать токен")
    p_issue.add_argument("--label", required=True, help="кому/зачем (метка)")
    p_issue.set_defaults(func=_cmd_issue)

    p_revoke = sub.add_parser("revoke", help="отозвать токен по id")
    p_revoke.add_argument("id")
    p_revoke.set_defaults(func=_cmd_revoke)

    p_list = sub.add_parser("list", help="список выданных токенов")
    p_list.set_defaults(func=_cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
