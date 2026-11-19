import pytest

from app.classifier import classify, extract_paths, signature
from app.llm import Analyzer, AnalysisContext, HeuristicProvider, parse_result
from app.security import api_key_ok, redact, validate_patch, verify_github_signature


@pytest.mark.parametrize("logs,cat", [
    ("ModuleNotFoundError: No module named 'yaml'", "dependency"),
    ("FAILED tests/test_a.py::t - AssertionError", "test_failure"),
    ("src/a.ts(3,1): error TS2322: bad", "type_error"),
    ("npm ERR! 404 Not Found", "dependency"),
    ("Error: connection reset by peer", "network_flaky"),
    ("something odd", "unknown"),
])
def test_classify(logs, cat):
    assert classify(logs)[0] == cat


def test_signature_ignores_noise():
    a = "2025-01-01T10:00:00Z ERROR line 42 in /home/runner/work/x/x/app.py: boom 0xdeadbeef"
    b = "2025-06-09T11:30:11Z ERROR line 97 in /tmp/abc/app.py: boom 0xcafebabe1"
    assert signature(a) == signature(b)
    assert signature(a) != signature("ERROR something totally different")


def test_extract_paths_skips_noise():
    logs = "File /home/runner/work/shop/shop/calc.py line 2\n/usr/lib/site-packages/x.py\nsrc/app.ts"
    assert extract_paths(logs) == ["calc.py", "src/app.ts"]


def test_redact_secrets():
    text = "token=abc123 ghp_" + "a" * 30 + " Authorization: Bearer xyz AKIAABCDEFGHIJKLMNOP ok"
    out = redact(text)
    assert "abc123" not in out and "ghp_" not in out and "xyz" not in out and "AKIA" not in out
    assert out.endswith("ok")


def test_signature_verify():
    import hashlib, hmac
    body = b"{}"
    good = "sha256=" + hmac.new(b"s", body, hashlib.sha256).hexdigest()
    assert verify_github_signature("s", body, good)
    assert not verify_github_signature("s", body, "sha256=bad")
    assert not verify_github_signature("", body, good)  # no secret configured = reject
    assert not verify_github_signature("s", body, None)


def test_api_key():
    assert api_key_ok("k", "k") and not api_key_ok("k", "x") and not api_key_ok("", "")


@pytest.mark.parametrize("path", ["../etc/passwd", "/abs/x", ".github/workflows/ci.yml", ".env", "a/.env.local", ".git/config"])
def test_validate_patch_blocks(path):
    assert validate_patch({"edits": [{"path": path, "search": "", "replace": "x"}]})


def test_validate_patch_ok_and_limits():
    assert validate_patch({"edits": [{"path": "src/a.py", "search": "a", "replace": "b"}]}) == []
    assert validate_patch({"edits": []}) and validate_patch(None)
    assert validate_patch({"edits": [{"path": f"f{i}.py"} for i in range(6)]})


def test_heuristic_missing_module():
    r = HeuristicProvider().analyze(AnalysisContext("ModuleNotFoundError: No module named 'yaml'", "dependency"))
    assert r.edits == [{"path": "requirements.txt", "search": "", "replace": "pyyaml"}]


def test_parse_result_strips_code_fences():
    r = parse_result('```json\n{"root_cause":"x","confidence":7,"edits":[]}\n```', "p")
    assert r.root_cause == "x" and r.confidence == 1.0


def test_analyzer_falls_back_on_error():
    class Boom:
        name = "boom"
        def analyze(self, ctx):
            raise RuntimeError("quota")
    r = Analyzer(Boom()).analyze(AnalysisContext("ModuleNotFoundError: No module named 'yaml'", "dependency"))
    assert r.provider == "heuristic" and r.edits


def test_postgres_url_is_normalised():
    from app.config import Settings
    assert Settings(database_url="postgres://u:p@h/db").database_url == "postgresql+psycopg2://u:p@h/db"
    assert Settings(database_url="postgresql://u:p@h/db").database_url == "postgresql+psycopg2://u:p@h/db"
    assert Settings(database_url="sqlite:///x.db").database_url == "sqlite:///x.db"
