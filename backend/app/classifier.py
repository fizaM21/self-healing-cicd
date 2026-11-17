import hashlib
import re

# Ordered: first match wins. Each rule = (category, patterns).
RULES: list[tuple[str, list[str]]] = [
    ("secret_config", [r"(?i)missing (required )?(env|environment)", r"(?i)KeyError: ['\"][A-Z_]{3,}['\"]", r"(?i)permission denied \(publickey\)", r"(?i)bad credentials"]),
    ("dependency", [r"ModuleNotFoundError", r"ImportError: cannot import", r"Cannot find module", r"(?i)no matching distribution", r"(?i)could not find a version that satisfies", r"npm ERR! 404", r"(?i)unresolved dependency"]),
    ("timeout", [r"(?i)timed out", r"(?i)timeout exceeded", r"(?i)the job running on runner .* has exceeded"]),
    ("network_flaky", [r"(?i)connection (reset|refused)", r"(?i)temporary failure in name resolution", r"(?i)ECONNRESET", r"(?i)503 service unavailable", r"(?i)rate limit"]),
    ("type_error", [r"error TS\d+", r"(?i)mypy.*error", r"(?i)incompatible types", r"(?i)pyright.*error"]),
    ("lint", [r"(?i)flake8", r"(?i)\bruff\b.*[A-Z]\d{3}", r"(?i)eslint", r"(?i)prettier.*(check|failed)", r"(?i)black.*would reformat", r"\bE\d{3}\b.*", r"(?i)trailing whitespace"]),
    ("test_failure", [r"AssertionError", r"(?m)^FAILED ", r"(?i)\d+ failed", r"(?i)tests? failed", r"(?i)expected .* (to|but) ", r"✕|●.*›"]),
    ("build", [r"(?i)build failed", r"(?i)compilation (error|failed)", r"SyntaxError", r"(?i)failed to compile", r"(?i)error: command .* failed", r"(?i)docker build.*error"]),
]

_ERROR_LINE = re.compile(r"(?i)(error|exception|failed|assert|cannot|not found|no module)")


def classify(logs: str) -> tuple[str, float]:
    """Return (category, confidence). Confidence grows with number of matching patterns."""
    for category, patterns in RULES:
        hits = sum(1 for p in patterns if re.search(p, logs))
        if hits:
            return category, min(0.55 + 0.15 * hits, 0.95)
    return "unknown", 0.2


def normalize(logs: str, max_lines: int = 12) -> str:
    """Strip noise (times, ids, numbers, paths) so the same failure looks the same twice."""
    lines = [ln for ln in logs.splitlines() if _ERROR_LINE.search(ln)][-max_lines:]
    out = []
    for ln in lines:
        ln = ln.lower()
        ln = re.sub(r"\d{4}-\d{2}-\d{2}t[\d:.]+z", "", ln)
        ln = re.sub(r"0x[0-9a-f]+|\b[0-9a-f]{7,40}\b", "H", ln)
        ln = re.sub(r"(/[\w.\-]+)+/", "/", ln)
        ln = re.sub(r"\d+", "N", ln)
        out.append(ln.strip())
    return "\n".join(out)


def signature(logs: str) -> str:
    return hashlib.sha256(normalize(logs).encode()).hexdigest()


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z_]{3,}", text))


def jaccard(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


_PATH = re.compile(r"([\w./\-]+\.(?:py|js|jsx|ts|tsx|json|toml|txt|yml|yaml|cfg|ini))")


def extract_paths(logs: str, limit: int = 5) -> list[str]:
    """Repo files mentioned in the log (relative, de-duplicated)."""
    seen: list[str] = []
    for m in _PATH.findall(logs):
        p = re.sub(r"^.*?/work/[^/]+/[^/]+/", "", m).lstrip("./")
        if not p or p.startswith("/") or "site-packages" in p or "node_modules" in p or ".." in p:
            continue
        if p not in seen:
            seen.append(p)
        if len(seen) >= limit:
            break
    return seen
