import json

PROJECT = "[project]\nname = 'x'\n"


def write(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_empty(run, tmp_path):
    assert run("discover-envs.sh", cwd=tmp_path) == "[]"


def test_services_and_depth(run, tmp_path):
    write(tmp_path / "pyproject.toml", PROJECT)
    write(tmp_path / "services/auth/workdir/pyproject.toml", PROJECT)
    write(tmp_path / "services/rag/pyproject.toml", PROJECT)
    write(tmp_path / "services/rag/deep/pyproject.toml", PROJECT)  # глубже → игнор
    write(tmp_path / "services/web/package.json", "{}")  # не python → игнор
    assert json.loads(run("discover-envs.sh", cwd=tmp_path)) == [
        ".",
        "services/auth/workdir",
        "services/rag",
    ]


def test_pytest_config_counts_without_project(run, tmp_path):
    write(tmp_path / "pyproject.toml", "[tool.pytest.ini_options]\ntestpaths = ['tests']\n")
    assert json.loads(run("discover-envs.sh", cwd=tmp_path)) == ["."]


def test_tooling_only_ignored(run, tmp_path):
    write(tmp_path / "pyproject.toml", "[tool.ruff]\n")  # ни project, ни pytest
    assert run("discover-envs.sh", cwd=tmp_path) == "[]"
