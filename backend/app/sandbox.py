"""Apply a patch on a throw-away clone, run the tests, optionally push a branch.

docker mode: tests run in a container (limited CPU/RAM/PIDs, no host mounts except the clone).
local mode : plain subprocess with a scrubbed environment. For dev/tests only - NOT isolated.
"""
import base64
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .security import redact, validate_patch


@dataclass
class SandboxResult:
    passed: bool
    output: str
    workdir: str | None = None
    command: str = ""


class SandboxError(Exception):
    pass


def _git(args: list[str], cwd: str | None = None, token: str = "", timeout: int = 120) -> str:
    cmd = ["git"]
    if token:  # auth through a header: token never ends up in the URL or .git/config
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        cmd += ["-c", f"http.extraheader=AUTHORIZATION: basic {basic}"]
    cmd += args
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    if p.returncode != 0:
        raise SandboxError(redact(f"git {args[0]} failed: {p.stderr.strip()[:500]}"))
    return p.stdout


def apply_edits(root: str, edits: list[dict]) -> None:
    rootp = Path(root).resolve()
    for e in edits:
        target = (rootp / e["path"]).resolve()
        if rootp not in target.parents:  # second line of defence after validate_patch
            raise SandboxError(f"path escapes sandbox: {e['path']}")
        search, replace = e.get("search", ""), e.get("replace", "")
        if search == "":
            target.parent.mkdir(parents=True, exist_ok=True)
            existing = target.read_text() if target.exists() else ""
            sep = "" if not existing or existing.endswith("\n") else "\n"
            target.write_text(existing + sep + replace + ("" if replace.endswith("\n") else "\n"))
        else:
            if not target.exists():
                raise SandboxError(f"file not found: {e['path']}")
            text = target.read_text()
            if text.count(search) != 1:
                raise SandboxError(f"search text must match exactly once in {e['path']} (found {text.count(search)})")
            target.write_text(text.replace(search, replace))


def detect_test_command(root: str) -> tuple[str, str]:
    """Return (command, runtime) where runtime is 'python' or 'node'."""
    r = Path(root)
    if (r / "package.json").exists():
        return "npm ci && npm test --silent" if (r / "package-lock.json").exists() else "npm install && npm test --silent", "node"
    pre = "pip install -q -r requirements.txt && " if (r / "requirements.txt").exists() else ""
    return f"{pre}python -m pytest -q", "python"


class Sandbox:
    def __init__(self, settings):
        self.s = settings

    def remote(self, repo: str) -> str:
        return self.s.git_remote_template.format(repo=repo)

    def run(self, repo: str, sha: str, branch: str, edits: list[dict], test_command: str | None) -> SandboxResult:
        problems = validate_patch({"edits": edits})
        if problems:
            raise SandboxError("; ".join(problems))
        work = tempfile.mkdtemp(prefix="selfheal-", dir=self.s.sandbox_workdir or None)
        try:
            _git(["clone", "--quiet", self.remote(repo), work], token=self.s.github_token)
            if sha:
                _git(["checkout", "--quiet", sha], cwd=work)
            _git(["checkout", "--quiet", "-b", branch], cwd=work)
            apply_edits(work, edits)
            auto_cmd, runtime = detect_test_command(work)
            cmd = test_command or self.s.default_test_command or auto_cmd
            passed, out = self._run_tests(work, cmd, runtime)
            if passed:
                _git(["add", "-A"], cwd=work)
                _git(["-c", "user.name=self-healing-bot", "-c", "user.email=bot@selfheal.local",
                      "commit", "--quiet", "-m", "fix(ci): automated self-healing patch"], cwd=work)
            return SandboxResult(passed, redact(out[-6000:]), work, cmd)
        except Exception:
            shutil.rmtree(work, ignore_errors=True)
            raise

    def _run_tests(self, work: str, cmd: str, runtime: str) -> tuple[bool, str]:
        timeout = self.s.sandbox_timeout_s
        try:
            if self.s.sandbox_mode == "docker":
                image = self.s.sandbox_image_node if runtime == "node" else self.s.sandbox_image_python
                full = ["docker", "run", "--rm", "--cpus", "1", "--memory", "1g", "--pids-limit", "256",
                        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                        "-v", f"{work}:/work", "-w", "/work", image, "sh", "-c", cmd]
                p = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
            else:
                # scrubbed env: no tokens or API keys leak into the code under test
                env = {"PATH": os.environ.get("PATH", ""), "HOME": work, "PYTHONDONTWRITEBYTECODE": "1"}
                p = subprocess.run(["sh", "-c", cmd], cwd=work, capture_output=True, text=True,
                                   timeout=timeout, env=env)
            return p.returncode == 0, (p.stdout or "") + (p.stderr or "")
        except subprocess.TimeoutExpired:
            return False, f"tests timed out after {timeout}s"
        except FileNotFoundError as exc:
            raise SandboxError(f"sandbox runtime missing: {exc.filename}") from exc

    def push(self, result: SandboxResult, repo: str, branch: str) -> None:
        assert result.workdir
        _git(["push", "--quiet", "origin", branch], cwd=result.workdir, token=self.s.github_token)

    @staticmethod
    def cleanup(result: SandboxResult | None) -> None:
        if result and result.workdir:
            shutil.rmtree(result.workdir, ignore_errors=True)
