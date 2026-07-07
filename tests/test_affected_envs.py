import json
import subprocess

ALL = '[".","services/auth","services/rag"]'


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def base_commit(tmp_path):
    git(tmp_path, "init", "-qb", "main")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "README.md").write_text("x")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "init")
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True)
    return result.stdout.strip()


def change(tmp_path, rel):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("changed")  # отлично от base, иначе нет диффа (README уже "x")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "c")


def test_service_only(run, tmp_path):
    base = base_commit(tmp_path)
    change(tmp_path, "services/auth/main.py")
    assert json.loads(run("affected-envs.sh", ALL, base, cwd=tmp_path)) == ["services/auth"]


def test_docs_only_runs_nothing(run, tmp_path):
    base = base_commit(tmp_path)
    change(tmp_path, "README.md")
    assert run("affected-envs.sh", ALL, base, cwd=tmp_path) == "[]"


def test_shared_file_runs_all(run, tmp_path):
    base = base_commit(tmp_path)
    change(tmp_path, "pyproject.toml")  # вне сервисных окружений → перестраховка
    assert json.loads(run("affected-envs.sh", ALL, base, cwd=tmp_path)) == json.loads(ALL)


def test_empty_base_runs_all(run, tmp_path):
    base_commit(tmp_path)
    assert json.loads(run("affected-envs.sh", ALL, "", cwd=tmp_path)) == json.loads(ALL)
