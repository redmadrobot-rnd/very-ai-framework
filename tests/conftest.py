import pathlib
import subprocess

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / ".github" / "scripts"


@pytest.fixture
def run():
    """Запускает CI-скрипт через bash и возвращает stdout (падает на ненулевом коде)."""

    def _run(script, *args, cwd):
        result = subprocess.run(
            ["bash", str(SCRIPTS / script), *args],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    return _run
