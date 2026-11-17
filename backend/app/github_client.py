import base64

import httpx

API = "https://api.github.com"


class GitHubClient:
    def __init__(self, token: str, max_log_chars: int = 15000):
        self.token = token
        self.max_log_chars = max_log_chars

    def _h(self, accept="application/vnd.github+json"):
        h = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def get_failed_run_logs(self, repo: str, run_id: int) -> str:
        with httpx.Client(timeout=60, follow_redirects=True) as c:
            r = c.get(f"{API}/repos/{repo}/actions/runs/{run_id}/jobs", headers=self._h())
            r.raise_for_status()
            parts = []
            for job in r.json().get("jobs", []):
                if job.get("conclusion") != "failure":
                    continue
                lr = c.get(f"{API}/repos/{repo}/actions/jobs/{job['id']}/logs", headers=self._h())
                if lr.status_code == 200:
                    parts.append(f"=== job: {job['name']} ===\n{lr.text[-self.max_log_chars:]}")
            return "\n".join(parts)

    def get_file(self, repo: str, path: str, ref: str) -> str | None:
        with httpx.Client(timeout=30) as c:
            r = c.get(f"{API}/repos/{repo}/contents/{path}", params={"ref": ref}, headers=self._h())
            if r.status_code != 200:
                return None
            data = r.json()
            if isinstance(data, dict) and data.get("encoding") == "base64":
                return base64.b64decode(data["content"]).decode("utf-8", "replace")
            return None

    def create_pr(self, repo: str, head: str, base: str, title: str, body: str) -> str:
        with httpx.Client(timeout=30) as c:
            r = c.post(f"{API}/repos/{repo}/pulls", headers=self._h(),
                       json={"title": title, "head": head, "base": base, "body": body})
            r.raise_for_status()
            return r.json()["html_url"]
