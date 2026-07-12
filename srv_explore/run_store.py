"""История прогонов агента srv-explore (для монитора задач и лога сессий в /admin).

Не растущий лог, а самоподрезающийся стейт: храним только последние N сессий НА
ЮЗЕРА (label токена). Кап N задаётся в конфиге (`SRV_EXPLORE_HISTORY_PER_USER`,
дефолт 15) — размер файла ограничен по построению (юзеров × N), ротация не нужна.

Файл — env `SRV_EXPLORE_RUNS` или `/var/lib/srv-explore/runs.json`. Формат:
`{ "<label>": [ {прогон}, … ≤N ] }`. Запись атомарна (tmp + rename).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_RUNS = "/var/lib/srv-explore/runs.json"
DEFAULT_CAP = 15
RESULT_MAX = 8000  # findings бывают жирные — обрезаем, чтобы файл не пух


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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
    env: str
    status: str  # running | done | error
    started: str
    finished: str | None = None
    result: str | None = None
    error: str | None = None


class RunStore:
    """Последние N прогонов на юзера. In-memory (по юзеру + индекс) + JSON-файл."""

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path or os.environ.get("SRV_EXPLORE_RUNS", DEFAULT_RUNS))
        self.cap = _cap_from_env()
        self._by_user: dict[str, list[RunRecord]] = {}
        self._index: dict[str, RunRecord] = {}
        self._load()

    def _reindex(self) -> None:
        self._index = {r.id: r for runs in self._by_user.values() for r in runs}

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            self._by_user = {
                user: [RunRecord(**r) for r in runs] for user, runs in raw.items()
            }
            self._reindex()
        except (OSError, ValueError, TypeError):
            self._by_user = {}
            self._index = {}

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
            os.replace(tmp, self.path)  # атомарно — файл не побьётся при краше
        except OSError:
            pass  # история не должна ронять прогон

    def start(self, run_id: str, task: str, label: str, env: str) -> RunRecord:
        rec = RunRecord(
            id=run_id,
            task=task,
            label=label,
            env=env,
            status="running",
            started=_now(),
        )
        runs = self._by_user.setdefault(label, [])
        runs.append(rec)
        # оставить только N новейших этого юзера, выбывшие убрать из индекса
        if len(runs) > self.cap:
            for dropped in runs[: len(runs) - self.cap]:
                self._index.pop(dropped.id, None)
            del runs[: len(runs) - self.cap]
        self._index[run_id] = rec
        self._save()
        return rec

    def finish(
        self, run_id: str, result: str | None = None, error: str | None = None
    ) -> None:
        rec = self._index.get(run_id)
        if rec is None:
            return
        rec.status = "error" if error is not None else "done"
        rec.finished = _now()
        rec.result = _clip(result)
        rec.error = _clip(error)
        self._save()

    def get(self, run_id: str) -> RunRecord | None:
        return self._index.get(run_id)

    def list_recent(self, limit: int = 50) -> list[RunRecord]:
        allruns = [r for runs in self._by_user.values() for r in runs]
        return sorted(allruns, key=lambda r: r.started, reverse=True)[:limit]
