from sqlalchemy import select
from sqlalchemy.orm import Session

from .classifier import jaccard
from .models import FailureMemory, now

SIMILARITY_THRESHOLD = 0.6


def lookup(db: Session, signature: str, normalized: str) -> dict:
    """Find past failures. exact = same signature that worked before; similar = close matches."""
    exact = db.scalar(select(FailureMemory).where(FailureMemory.signature == signature))
    exact_hit = exact if exact and exact.successes > exact.failures and exact.patch else None

    similar = []
    for m in db.scalars(select(FailureMemory).where(FailureMemory.successes > 0)):
        if m.signature == signature:
            continue
        score = jaccard(normalized, m.normalized)
        if score >= SIMILARITY_THRESHOLD:
            similar.append((score, m))
    similar.sort(key=lambda x: -x[0])
    return {
        "exact": exact_hit,
        "similar": [m for _, m in similar[:3]],
    }


def record_outcome(db: Session, *, signature: str, category: str, normalized: str,
                   root_cause: str, patch: dict | None, success: bool) -> FailureMemory:
    m = db.scalar(select(FailureMemory).where(FailureMemory.signature == signature))
    if m is None:
        m = FailureMemory(signature=signature, category=category, normalized=normalized, successes=0, failures=0)
        db.add(m)
    m.root_cause = root_cause or m.root_cause
    if success:
        m.successes += 1
        m.patch = patch  # only a patch that actually passed is worth remembering
    else:
        m.failures += 1
    m.last_seen = now()
    db.commit()
    return m
