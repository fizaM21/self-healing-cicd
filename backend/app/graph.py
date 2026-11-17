"""Two LangGraph workflows.

analysis_graph : collect_logs -> classify -> recall_memory -> analyze -> validate -> propose
                 (stops at 'awaiting_approval' - a human must approve)
recovery_graph : sandbox_test -> (pass) create_pr / (fail) record_failure
Splitting at the approval gate keeps the process stateless: the API (not a paused
process) holds the waiting state in PostgreSQL, so restarts never lose an approval.
"""
import functools
import logging
from typing import TypedDict

from langgraph.graph import END, StateGraph

from . import memory
from .classifier import classify, extract_paths, normalize, signature
from .deps import Deps
from .llm import AnalysisContext
from .models import AuditLog, Incident
from .sandbox import Sandbox, SandboxError, SandboxResult
from .security import redact, validate_patch

log = logging.getLogger(__name__)


class State(TypedDict, total=False):
    incident_id: int
    error: str | None
    normalized: str
    memory_hit: bool
    sandbox: SandboxResult


def _audit(db, incident_id, action, detail="", actor="system"):
    db.add(AuditLog(incident_id=incident_id, actor=actor, action=action, detail=detail[:2000]))
    db.commit()


def safe(name):
    """Wrap a node: any exception marks the incident 'error' and stops the graph."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(state: State, deps: Deps) -> State:
            if state.get("error"):
                return {}
            try:
                return fn(state, deps) or {}
            except Exception as exc:
                msg = redact(f"{name}: {type(exc).__name__}: {exc}")[:800]
                log.exception("node %s failed", name)
                with deps.session_factory() as db:
                    inc = db.get(Incident, state["incident_id"])
                    inc.status, inc.error = "error", msg
                    _audit(db, inc.id, "error", msg)
                return {"error": msg}
        return wrapper
    return deco


# ---------------- analysis graph ----------------
@safe("collect_logs")
def collect_logs(state, deps):
    with deps.session_factory() as db:
        inc = db.get(Incident, state["incident_id"])
        inc.status = "analyzing"
        if not inc.logs:
            inc.logs = deps.github.get_failed_run_logs(inc.repo, inc.run_id)
        inc.logs = redact(inc.logs)[-deps.settings.max_log_chars:]
        db.commit()
        _audit(db, inc.id, "logs_collected", f"{len(inc.logs)} chars")


@safe("classify")
def classify_node(state, deps):
    with deps.session_factory() as db:
        inc = db.get(Incident, state["incident_id"])
        inc.category, conf = classify(inc.logs)
        inc.signature = signature(inc.logs)
        db.commit()
        _audit(db, inc.id, "classified", f"{inc.category} ({conf:.2f})")
        return {"normalized": normalize(inc.logs)}


@safe("recall_memory")
def recall_memory(state, deps):
    return {}  # lookup happens inside analyze so the result stays in one place


@safe("analyze")
def analyze(state, deps):
    with deps.session_factory() as db:
        inc = db.get(Incident, state["incident_id"])
        found = memory.lookup(db, inc.signature, state["normalized"])
        if found["exact"]:  # seen before and the fix worked: reuse it, skip the LLM call
            m = found["exact"]
            inc.root_cause, inc.confidence = m.root_cause, min(0.6 + 0.1 * m.successes, 0.95)
            inc.patch, inc.patch_source, inc.llm_provider = m.patch, "memory", "memory"
            db.commit()
            _audit(db, inc.id, "memory_hit", f"reusing fix that worked {m.successes}x")
            return {"memory_hit": True}

        files = {}
        for p in extract_paths(inc.logs):
            content = deps.github.get_file(inc.repo, p, inc.head_sha or inc.branch)
            if content is not None:
                files[p] = redact(content)
        hints = [f"{m.root_cause} -> {m.patch}" for m in found["similar"]]
        res = deps.analyzer.analyze(AnalysisContext(inc.logs, inc.category, files, hints))
        inc.root_cause, inc.confidence, inc.llm_provider = res.root_cause, res.confidence, res.provider
        inc.patch = {"edits": res.edits, "explanation": res.explanation} if res.edits else None
        inc.patch_source = "llm" if res.provider != "heuristic" else "heuristic"
        db.commit()
        _audit(db, inc.id, "analyzed", f"provider={res.provider} edits={len(res.edits)}")


@safe("validate")
def validate(state, deps):
    with deps.session_factory() as db:
        inc = db.get(Incident, state["incident_id"])
        if inc.patch is None:
            return
        problems = validate_patch(inc.patch)
        if problems:
            _audit(db, inc.id, "patch_rejected_by_policy", "; ".join(problems))
            inc.patch = None
            inc.error = "Patch blocked by safety policy: " + "; ".join(problems)
            db.commit()


@safe("propose")
def propose(state, deps):
    with deps.session_factory() as db:
        inc = db.get(Incident, state["incident_id"])
        inc.status = "awaiting_approval" if inc.patch else "needs_manual"
        db.commit()
        _audit(db, inc.id, "proposed" if inc.patch else "needs_manual")


def _route(next_node):
    return lambda s: END if s.get("error") else next_node


def build_analysis_graph(deps: Deps):
    g = StateGraph(State)
    nodes = [("collect_logs", collect_logs), ("classify", classify_node), ("recall_memory", recall_memory),
             ("analyze", analyze), ("validate", validate), ("propose", propose)]
    for name, fn in nodes:
        g.add_node(name, functools.partial(fn, deps=deps))
    g.set_entry_point("collect_logs")
    for (a, _), (b, _) in zip(nodes, nodes[1:]):
        g.add_conditional_edges(a, _route(b), {b: b, END: END})
    g.add_edge("propose", END)
    return g.compile()


# ---------------- recovery graph ----------------
@safe("sandbox_test")
def sandbox_test(state, deps):
    with deps.session_factory() as db:
        inc = db.get(Incident, state["incident_id"])
        inc.status = "applying"
        db.commit()
        branch = f"selfheal/run-{inc.run_id}"
        inc.status = "testing"
        db.commit()
        res = deps.sandbox.run(inc.repo, inc.head_sha, branch, inc.patch["edits"], inc.test_command)
        inc.sandbox_result = {"passed": res.passed, "command": res.command, "output": res.output}
        inc.attempts = 0
        db.commit()
        _audit(db, inc.id, "sandbox_tests", "passed" if res.passed else "failed")
        return {"sandbox": res}


def after_tests(state):
    if state.get("error"):
        return END
    return "create_pr" if state["sandbox"].passed else "record_failure"


@safe("create_pr")
def create_pr(state, deps):
    res = state["sandbox"]
    try:
        with deps.session_factory() as db:
            inc = db.get(Incident, state["incident_id"])
            branch = f"selfheal/run-{inc.run_id}"
            deps.sandbox.push(res, inc.repo, branch)
            body = (f"## Automated fix for failed run #{inc.run_id}\n\n**Category:** {inc.category}\n\n"
                    f"**Root cause:** {inc.root_cause}\n\n**Explanation:** {(inc.patch or {}).get('explanation', '')}\n\n"
                    f"Tests passed in the sandbox (`{res.command}`). Approved by a human reviewer.\n\n"
                    f"_Generated by self-healing-cicd. Review before merging._")
            inc.pr_url = deps.github.create_pr(inc.repo, branch, inc.branch,
                                               f"fix(ci): {inc.category} in run #{inc.run_id}", body)
            inc.status = "pr_created"
            db.commit()
            memory.record_outcome(db, signature=inc.signature, category=inc.category,
                                  normalized=normalize(inc.logs), root_cause=inc.root_cause or "",
                                  patch=inc.patch, success=True)
            _audit(db, inc.id, "pr_created", inc.pr_url)
    finally:
        Sandbox.cleanup(res)


@safe("record_failure")
def record_failure(state, deps):
    res = state["sandbox"]
    try:
        with deps.session_factory() as db:
            inc = db.get(Incident, state["incident_id"])
            inc.status = "failed"
            db.commit()
            memory.record_outcome(db, signature=inc.signature, category=inc.category,
                                  normalized=normalize(inc.logs), root_cause=inc.root_cause or "",
                                  patch=inc.patch, success=False)
            _audit(db, inc.id, "recovery_failed", "sandbox tests did not pass")
    finally:
        Sandbox.cleanup(res)


def build_recovery_graph(deps: Deps):
    g = StateGraph(State)
    for name, fn in [("sandbox_test", sandbox_test), ("create_pr", create_pr), ("record_failure", record_failure)]:
        g.add_node(name, functools.partial(fn, deps=deps))
    g.set_entry_point("sandbox_test")
    g.add_conditional_edges("sandbox_test", after_tests,
                            {"create_pr": "create_pr", "record_failure": "record_failure", END: END})
    g.add_edge("create_pr", END)
    g.add_edge("record_failure", END)
    return g.compile()
