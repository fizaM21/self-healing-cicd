import hashlib
import hmac
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm import AnalysisResult
from app.main import create_app, make_deps

KEY = "test-key"
SECRET = "whsec"
H = {"X-API-Key": KEY}


def sh(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


class FakeGitHub:
    def __init__(self, files):
        self.files, self.prs = files, []

    def get_failed_run_logs(self, repo, run_id):
        return "unused"

    def get_file(self, repo, path, ref):
        return self.files.get(path)

    def create_pr(self, repo, head, base, title, body):
        self.prs.append((repo, head, base, title))
        return f"https://github.com/{repo}/pull/{len(self.prs)}"


class FakeAnalyzer:
    def __init__(self, edits):
        self.edits, self.calls = edits, 0

    def analyze(self, ctx):
        self.calls += 1
        return AnalysisResult("add() subtracts instead of adding", 0.9, "Fix operator.", self.edits, "fake")


FIX = [{"path": "calc.py", "search": "return a - b", "replace": "return a + b"}]
LOGS = "FAILED test_calc.py::test_add - AssertionError: assert -1 == 5\nE   assert -1 == 5 in calc.py"


@pytest.fixture
def remote(tmp_path):
    """A real bare git repo that fails its own tests (add() is wrong)."""
    work = tmp_path / "work"
    work.mkdir()
    (work / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (work / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    sh("git", "init", "-q", "-b", "main", cwd=work)
    sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A", cwd=work)
    sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init", cwd=work)
    bare = tmp_path / "remotes" / "acme" / "shop.git"
    bare.parent.mkdir(parents=True)
    sh("git", "clone", "-q", "--bare", str(work), str(bare))
    return {"template": str(tmp_path / "remotes" / "{repo}.git"), "bare": bare,
            "files": {"calc.py": (work / "calc.py").read_text()}}


def build(tmp_path, remote, edits):
    s = Settings(database_url=f"sqlite:///{tmp_path}/t.db", api_key=KEY, github_webhook_secret=SECRET,
                 git_remote_template=remote["template"], sandbox_mode="local", sandbox_timeout_s=120)
    deps = make_deps(s)
    deps.github, deps.analyzer = FakeGitHub(remote["files"]), FakeAnalyzer(edits)
    return create_app(s, deps), deps


@pytest.fixture
def ctx(tmp_path, remote):
    app, deps = build(tmp_path, remote, FIX)
    with TestClient(app) as c:
        yield c, deps, remote


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def failed_run_payload(run_id=1):
    return {"action": "completed", "repository": {"full_name": "acme/shop"},
            "workflow_run": {"id": run_id, "name": "CI", "conclusion": "failure",
                             "head_branch": "main", "head_sha": ""}}
