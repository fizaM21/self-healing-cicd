import json
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import Settings, get_settings
from .deps import Deps
from .github_client import GitHubClient
from .graph import build_analysis_graph, build_recovery_graph
from .llm import make_analyzer
from .models import AuditLog, Base, FailureMemory, Incident
from .sandbox import Sandbox
from .security import api_key_ok, redact, verify_github_signature

log = logging.getLogger("selfheal")
STATUSES = ["detected", "analyzing", "awaiting_approval", "needs_manual", "rejected",
            "applying", "testing", "pr_created", "failed", "error"]


def make_deps(settings: Settings) -> Deps:
    kw = {}
    if settings.database_url.startswith("sqlite"):
        kw = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in settings.database_url:
            kw["poolclass"] = StaticPool
    engine = create_engine(settings.database_url, pool_pre_ping=True, **kw)
    Base.metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        # create_all never alters existing tables. GitHub run ids exceed 32 bits.
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE incidents ALTER COLUMN run_id TYPE BIGINT"))
    return Deps(settings, sessionmaker(engine, expire_on_commit=False),
                GitHubClient(settings.github_token, settings.max_log_chars),
                make_analyzer(settings), Sandbox(settings))


def create_app(settings: Settings | None = None, deps: Deps | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.deps = deps or make_deps(settings)
        yield

    app = FastAPI(title="Self-Healing CI/CD", version="1.0.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
                       allow_methods=["GET", "POST"], allow_headers=["X-API-Key", "Content-Type"])
    app.state.request_count = {}

    @app.middleware("http")
    async def count(request: Request, call_next):
        resp = await call_next(request)
        key = (request.method, request.url.path if not request.url.path.split("/")[-1].isdigit() else "/incidents/{id}", resp.status_code)
        app.state.request_count[key] = app.state.request_count.get(key, 0) + 1
        return resp

    def get_deps(request: Request) -> Deps:
        return request.app.state.deps

    def get_db(d: Deps = Depends(get_deps)):
        with d.session_factory() as db:
            yield db

    def require_key(d: Deps = Depends(get_deps), x_api_key: str | None = Header(default=None)):
        if not api_key_ok(d.settings.api_key, x_api_key):
            raise HTTPException(401, "invalid or missing API key")

    # ---- background jobs ----
    def run_analysis(d: Deps, incident_id: int):
        build_analysis_graph(d).invoke({"incident_id": incident_id})

    def run_recovery(d: Deps, incident_id: int):
        build_recovery_graph(d).invoke({"incident_id": incident_id})

    def serialize(i: Incident, full=False) -> dict:
        out = {c: getattr(i, c) for c in ["id", "repo", "run_id", "workflow", "branch", "head_sha", "status",
                                          "category", "root_cause", "confidence", "patch_source", "llm_provider",
                                          "pr_url", "error", "created_at", "updated_at"]}
        out["has_patch"] = bool(i.patch)
        if full:
            out.update(logs=i.logs, patch=i.patch, sandbox_result=i.sandbox_result, test_command=i.test_command)
        return out

    # ---- public ----
    @app.get("/health")
    def health(db: Session = Depends(get_db)):
        db.execute(select(1))
        return {"status": "ok"}

    @app.post("/webhooks/github", status_code=202)
    async def github_webhook(request: Request, bg: BackgroundTasks, d: Deps = Depends(get_deps),
                             x_hub_signature_256: str | None = Header(default=None),
                             x_github_event: str | None = Header(default=None)):
        body = await request.body()
        if not verify_github_signature(d.settings.github_webhook_secret, body, x_hub_signature_256):
            raise HTTPException(401, "bad signature")
        if x_github_event == "ping":
            return {"ok": True}
        if x_github_event != "workflow_run":
            return {"ignored": x_github_event}
        payload = json.loads(body)
        run = payload.get("workflow_run", {})
        if payload.get("action") != "completed" or run.get("conclusion") != "failure":
            return {"ignored": "not a failed completed run"}
        repo = payload["repository"]["full_name"]
        if run.get("name", "").lower().startswith("self-heal"):  # never loop on our own workflow
            return {"ignored": "own workflow"}
        with d.session_factory() as db:
            inc = Incident(repo=repo, run_id=run["id"], workflow=run.get("name", ""),
                           branch=run.get("head_branch", "main"), head_sha=run.get("head_sha", ""))
            db.add(inc)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                return {"ignored": "duplicate run"}
            iid = inc.id
        bg.add_task(run_analysis, d, iid)
        return {"incident_id": iid}

    # ---- protected ----
    class ManualIncident(BaseModel):
        repo: str = Field(pattern=r"^[\w.\-]+/[\w.\-]+$")
        run_id: int
        branch: str = "main"
        head_sha: str = ""
        logs: str = Field(default="", max_length=200000)
        test_command: str | None = Field(default=None, max_length=500)

    @app.post("/incidents", status_code=202, dependencies=[Depends(require_key)])
    def create_incident(body: ManualIncident, bg: BackgroundTasks, d: Deps = Depends(get_deps), db: Session = Depends(get_db)):
        inc = Incident(repo=body.repo, run_id=body.run_id, branch=body.branch, head_sha=body.head_sha,
                       logs=redact(body.logs), test_command=body.test_command)
        db.add(inc)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "incident for this run already exists")
        bg.add_task(run_analysis, d, inc.id)
        return {"incident_id": inc.id}

    @app.get("/incidents", dependencies=[Depends(require_key)])
    def list_incidents(status: str | None = None, limit: int = 50, db: Session = Depends(get_db)):
        q = select(Incident).order_by(Incident.id.desc()).limit(min(limit, 200))
        if status:
            q = q.where(Incident.status == status)
        return [serialize(i) for i in db.scalars(q)]

    @app.get("/incidents/{incident_id}", dependencies=[Depends(require_key)])
    def get_incident(incident_id: int, db: Session = Depends(get_db)):
        inc = db.get(Incident, incident_id)
        if not inc:
            raise HTTPException(404, "not found")
        audit = db.scalars(select(AuditLog).where(AuditLog.incident_id == incident_id).order_by(AuditLog.id))
        return {**serialize(inc, full=True),
                "audit": [{"actor": a.actor, "action": a.action, "detail": a.detail, "at": a.created_at} for a in audit]}

    class Decision(BaseModel):
        reviewer: str = Field(min_length=1, max_length=100)
        comment: str = Field(default="", max_length=1000)

    def claim(db: Session, incident_id: int, new_status: str) -> bool:
        """Atomic compare-and-set: only one approve/reject can win."""
        r = db.execute(update(Incident).where(Incident.id == incident_id, Incident.status == "awaiting_approval")
                       .values(status=new_status))
        db.commit()
        return r.rowcount == 1

    @app.post("/incidents/{incident_id}/approve", status_code=202, dependencies=[Depends(require_key)])
    def approve(incident_id: int, body: Decision, bg: BackgroundTasks, d: Deps = Depends(get_deps), db: Session = Depends(get_db)):
        if not db.get(Incident, incident_id):
            raise HTTPException(404, "not found")
        if not claim(db, incident_id, "applying"):
            raise HTTPException(409, "incident is not awaiting approval")
        db.add(AuditLog(incident_id=incident_id, actor=body.reviewer, action="approved", detail=body.comment))
        db.commit()
        bg.add_task(run_recovery, d, incident_id)
        return {"status": "applying"}

    @app.post("/incidents/{incident_id}/reject", dependencies=[Depends(require_key)])
    def reject(incident_id: int, body: Decision, db: Session = Depends(get_db)):
        inc = db.get(Incident, incident_id)
        if not inc:
            raise HTTPException(404, "not found")
        if not claim(db, incident_id, "rejected"):
            raise HTTPException(409, "incident is not awaiting approval")
        db.add(AuditLog(incident_id=incident_id, actor=body.reviewer, action="rejected", detail=body.comment))
        db.commit()
        return {"status": "rejected"}

    @app.get("/stats", dependencies=[Depends(require_key)])
    def stats(db: Session = Depends(get_db)):
        counts = dict(db.execute(select(Incident.status, func.count()).group_by(Incident.status)).all())
        cats = dict(db.execute(select(Incident.category, func.count()).where(Incident.category.is_not(None))
                               .group_by(Incident.category)).all())
        total = sum(counts.values())
        done = counts.get("pr_created", 0)
        attempted = done + counts.get("failed", 0)
        return {"total": total, "by_status": counts, "by_category": cats,
                "recovery_rate": round(done / attempted, 3) if attempted else None,
                "memory_entries": db.scalar(select(func.count()).select_from(FailureMemory))}

    @app.get("/memory", dependencies=[Depends(require_key)])
    def list_memory(db: Session = Depends(get_db)):
        return [{"signature": m.signature[:12], "category": m.category, "root_cause": m.root_cause,
                 "successes": m.successes, "failures": m.failures, "last_seen": m.last_seen}
                for m in db.scalars(select(FailureMemory).order_by(FailureMemory.last_seen.desc()).limit(100))]

    @app.get("/metrics", response_class=PlainTextResponse, dependencies=[Depends(require_key)])
    def metrics(request: Request, db: Session = Depends(get_db)):
        lines = ["# TYPE selfheal_incidents gauge"]
        counts = dict(db.execute(select(Incident.status, func.count()).group_by(Incident.status)).all())
        lines += [f'selfheal_incidents{{status="{s}"}} {counts.get(s, 0)}' for s in STATUSES]
        lines += ["# TYPE selfheal_memory_entries gauge",
                  f"selfheal_memory_entries {db.scalar(select(func.count()).select_from(FailureMemory))}",
                  "# TYPE selfheal_http_requests_total counter"]
        for (m, p, code), n in sorted(request.app.state.request_count.items()):
            lines.append(f'selfheal_http_requests_total{{method="{m}",path="{p}",code="{code}"}} {n}')
        return "\n".join(lines) + "\n"

    return app


app = create_app()
