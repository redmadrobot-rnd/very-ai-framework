"""История завершённых прогонов: последние N сессий на юзера."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_RUNS = "/var/lib/srv-explore/runs.json"
DEFAULT_CAP = 15
RESULT_MAX = 8000


def _cap_from_env() -> int:
    try:
        n = int(os.environ.get("SRV_EXPLORE_HISTORY_PER_USER", DEFAULT_CAP))
        return n if n > 0 else DEFAULT_CAP
    except ValueError:
        return DEFAULT_CAP


def _clip(text: str | None) -> str | None:
    if text is None or len(text) <= RESULT_MAX:
        return text
    return text[:RESULT_MAX] + "\n…[обрезано]"


@dataclass
class RunRecord:
    id: str
    task: str
    label: str
    status: str
    started: str
    finished: str | None = None
    result: str | None = None
    error: str | None = None
    steps: list = field(default_factory=list)  # команды сессии: {cmd, ok, reason}


class RunStore:
    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path or os.environ.get("SRV_EXPLORE_RUNS", DEFAULT_RUNS))
        self.cap = _cap_from_env()
        self._by_user: dict[str, list[RunRecord]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            self._by_user = {
                user: [RunRecord(**r) for r in runs] for user, runs in raw.items()
            }
        except (OSError, ValueError, TypeError):
            self._by_user = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                user: [asdict(r) for r in runs] for user, runs in self._by_user.items()
            }
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            try:
                tmp.chmod(0o640)
            except OSError:
                pass
            os.replace(tmp, self.path)
        except OSError:
            pass

    def add(self, record: RunRecord) -> None:
        record.result = _clip(record.result)
        record.error = _clip(record.error)
        runs = self._by_user.setdefault(record.label, [])
        runs.append(record)
        if len(runs) > self.cap:
            del runs[: len(runs) - self.cap]
        self._save()

    def list_recent(self, limit: int = 50) -> list[RunRecord]:
        allruns = [r for runs in self._by_user.values() for r in runs]
        return sorted(allruns, key=lambda r: r.started, reverse=True)[:limit]
