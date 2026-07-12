"""История прогонов агента srv-explore (для монитора задач и лога сессий в /admin).

Каждый вызов `srv_explore(task)` = один прогон: задача, кем запущен (label токена),
окружение, статус (running/done/error), результат/ошибка. Персистится в JSONL, чтобы
история и статус задач переживали рестарт сервиса (job-реестр иначе in-memory).

Файл — env `SRV_EXPLORE_RUNS` или `/var/lib/srv-explore/runs.jsonl`.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_RUNS = "/var/lib/srv-explore/runs.jsonl"


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


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class RunStore:
    """История прогонов: in-memory dict + JSONL на диске (last-wins по id)."""

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path or os.environ.get("SRV_EXPLORE_RUNS", DEFAULT_RUNS))
        self._runs: dict[str, RunRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    r = json.loads(line)
                    self._runs[r["id"]] = RunRecord(**r)
        except (OSError, ValueError, TypeError):
            self._runs = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lines = [
                json.dumps(asdict(r), ensure_ascii=False) for r in self._runs.values()
            ]
            self.path.write_text(
                "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
            )
            try:
                self.path.chmod(0o640)
            except OSError:
                pass
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
        self._runs[run_id] = rec
        self._save()
        return rec

    def finish(
        self, run_id: str, result: str | None = None, error: str | None = None
    ) -> None:
        rec = self._runs.get(run_id)
        if rec is None:
            return
        rec.status = "error" if error is not None else "done"
        rec.finished = _now()
        rec.result = result
        rec.error = error
        self._save()

    def get(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    def list_recent(self, limit: int = 50) -> list[RunRecord]:
        return sorted(self._runs.values(), key=lambda r: r.started, reverse=True)[
            :limit
        ]
