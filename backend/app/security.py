import hashlib
import hmac
import re

# Patterns for secrets that must never reach the LLM, the DB or the dashboard.
_SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key)(\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(authorization:\s*(?:bearer|basic)\s+)\S+"),
]


def redact(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        if pat.groups >= 2:
            text = pat.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
        elif pat.groups == 1:
            text = pat.sub(lambda m: f"{m.group(1)}[REDACTED]", text)
        else:
            text = pat.sub("[REDACTED]", text)
    return text


def verify_github_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def api_key_ok(expected: str, provided: str | None) -> bool:
    if not expected or not provided:
        return False
    return hmac.compare_digest(expected.encode(), provided.encode())


# --- patch safety -----------------------------------------------------------
BLOCKED_PREFIXES = (".github/", ".git/", ".env")
MAX_EDITS = 5
MAX_EDIT_CHARS = 20000


def validate_patch(patch: dict | None) -> list[str]:
    """Return a list of problems. Empty list = patch is allowed."""
    if not patch or not isinstance(patch.get("edits"), list) or not patch["edits"]:
        return ["patch has no edits"]
    errors: list[str] = []
    if len(patch["edits"]) > MAX_EDITS:
        errors.append(f"too many edits (max {MAX_EDITS})")
    for i, e in enumerate(patch["edits"]):
        if not isinstance(e, dict) or not isinstance(e.get("path"), str):
            errors.append(f"edit {i}: bad shape")
            continue
        path = e["path"].replace("\\", "/")
        parts = [p for p in path.split("/") if p]
        if path.startswith("/") or ".." in parts:
            errors.append(f"edit {i}: unsafe path '{path}'")
        if any(path.startswith(b) or f"/{b}" in path for b in BLOCKED_PREFIXES):
            errors.append(f"edit {i}: path '{path}' is protected")
        if len(str(e.get("replace", ""))) > MAX_EDIT_CHARS or len(str(e.get("search", ""))) > MAX_EDIT_CHARS:
            errors.append(f"edit {i}: edit too large")
    return errors
