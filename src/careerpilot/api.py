"""Single local workspace API; historical identity contracts are opt-in fixtures."""

import hashlib
import logging
import time
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException

from careerpilot import application, i18n
from careerpilot.domain import DomainError, Principal
from careerpilot.growth_api import register_growth
from careerpilot.persistence import (
    SCHEMA_REVISION,
    CareerGoal,
    SessionToken,
    database_url,
    make_engine,
)
from careerpilot.platform_api import register_platform
from careerpilot.platform_models import RequestMetric, RoleGrant
from careerpilot.v1_api import register_v1
from careerpilot.v2_api import register_v2


class GoalInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)


class GoalOutput(GoalInput):
    id: str
    status: str
    version: int
    created_at: str


class LocalResetInput(BaseModel):
    confirmation: Literal["CLEAR LOCAL DATA"]


def create_app(url: str | None = None, *, local_only: bool = True) -> FastAPI:
    engine = make_engine(url or database_url())

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()

    app = FastAPI(title="CareerPilot", version="0.1.0", lifespan=lifespan)

    def error(request, status, code, message, retryable=False):
        # Fixed system explanations follow Accept-Language; raw input is never echoed.
        locale = i18n.negotiate(request.headers.get("accept-language"))
        return JSONResponse(
            status_code=status,
            content={
                "code": code,
                "message": i18n.error_message(code, message, status, locale),
                "details": [],
                "trace_id": request.state.trace_id,
                "retryable": retryable,
            },
            headers={"Content-Language": locale, "Vary": "Accept-Language"},
        )

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = str(uuid4())
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            response = error(request, 500, "INTERNAL_ERROR", "内部错误")
        response.headers["X-Trace-ID"] = request.state.trace_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; "
            "media-src 'self' blob:; frame-ancestors 'none'"
        )
        if request.url.path in {"/docs", "/redoc"}:
            # FastAPI's interactive docs load the upstream Swagger/ReDoc bundle.
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
                "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' data: "
                "https://fastapi.tiangolo.com; frame-ancestors 'none'"
            )
        route = getattr(request.scope.get("route"), "path", "unmatched")
        elapsed = int((time.monotonic() - start) * 1000)
        principal = getattr(request.state, "principal", None)
        try:
            with Session(engine) as metrics_db, metrics_db.begin():
                if principal and response.status_code == 403:
                    application.audit(
                        metrics_db, principal, "", "access.denied", 0, request.state.trace_id
                    )
                metrics_db.add(
                    RequestMetric(
                        tenant_id=principal.tenant_id if principal else "",
                        route=route,
                        status=response.status_code,
                        elapsed_ms=elapsed,
                        trace_id=request.state.trace_id,
                    )
                )
        except SQLAlchemyError:
            # Readiness must remain useful before a migration; no raw exception log.
            pass
        logging.getLogger("careerpilot").info(
            "request trace=%s status=%s elapsed_ms=%s",
            request.state.trace_id,
            response.status_code,
            elapsed,
        )
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return error(request, exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not reflect raw input, credentials, or private values into errors.
        return error(request, 422, "VALIDATION_ERROR", "请求字段或请求头无效")

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error(request, exc.status_code, "HTTP_ERROR", "请求无法处理")

    @app.exception_handler(IntegrityError)
    async def conflict(request, exc):
        return error(request, 409, "CONFLICT", "写入冲突，请刷新或以相同幂等键重试")

    def session_dependency():
        with Session(engine) as session:
            yield session

    Db = Annotated[Session, Depends(session_dependency)]
    bearer = HTTPBearer(auto_error=False)

    def contract_identity(
        request: Request,
        db: Db,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        if credentials is None:
            raise DomainError("UNAUTHENTICATED", "需要有效登录凭据", 401)
        digest = hashlib.sha256(credentials.credentials.encode()).hexdigest()
        token = db.get(SessionToken, digest)
        if token is None or token.expires_at <= int(time.time()):
            raise DomainError("UNAUTHENTICATED", "需要有效登录凭据", 401)
        grant = db.get(RoleGrant, (token.user_id, token.tenant_id))
        principal = Principal(token.user_id, token.tenant_id, grant.role if grant else "student")
        request.state.session_digest = digest
        request.state.principal = principal
        return principal

    def local_identity(request: Request):
        from careerpilot.local_workspace import principal

        owner = principal(engine)
        expected_owner = request.headers.get("x-workspace-id")
        if (
            expected_owner
            and expected_owner != owner.user_id
            and request.url.path != "/api/v1/local-workspace"
        ):
            raise DomainError("LOCAL_WORKSPACE_CHANGED", "本地工作区已重置，请刷新后继续。", 409)
        request.state.principal = owner
        return owner

    # Token/role contracts are retained only for explicit historical regression fixtures.
    # Normal startup always uses the local workspace and never resolves role grants.
    identity = local_identity if local_only else contract_identity
    Identity = Annotated[Principal, Depends(identity)]

    if local_only:

        @app.middleware("http")
        async def local_boundary(request: Request, call_next):
            if request.url.path.startswith("/api/"):
                from urllib.parse import urlsplit

                origin = request.headers.get("origin")
                permitted = request.url.hostname in {"localhost", "127.0.0.1", "::1", "testserver"}
                if origin:
                    parsed = urlsplit(origin)
                    permitted = (
                        permitted
                        and parsed.scheme in {"http", "https"}
                        and (parsed.netloc == request.headers.get("host"))
                    )
                if request.headers.get("sec-fetch-site") == "cross-site":
                    permitted = False
                if not permitted:
                    return JSONResponse(status_code=403, content={"code": "LOCAL_ACCESS_ONLY"})
            return await call_next(request)

    @app.get("/api/v1/local-workspace")
    def workspace(principal: Identity):
        return {"user_id": principal.user_id, "storage": "local", "requires_login": not local_only}

    if local_only:

        @app.post("/api/v1/local-workspace/reset", status_code=202)
        def reset_workspace(body: LocalResetInput, request: Request, db: Db, principal: Identity):
            from careerpilot import accounts

            result = accounts.request_deletion(db, principal, request.state.trace_id)
            db.commit()
            return result

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready(request: Request):
        try:
            with engine.connect() as connection:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
                if revision != SCHEMA_REVISION:
                    raise DomainError("NOT_READY", "数据库迁移未就绪", 503)
        except SQLAlchemyError:
            return error(request, 503, "NOT_READY", "数据库迁移未就绪", True)
        return {"status": "ready", "schema_revision": revision}

    @app.get("/api/v1/me")
    def me(principal: Identity):
        result = {
            "user_id": principal.user_id,
            "tenant_id": principal.tenant_id,
        }
        if not local_only:
            result["role"] = principal.role
        return result

    @app.post("/api/v1/auth/logout", status_code=204)
    def logout(
        db: Db,
        principal: Identity,
        credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)],
    ):
        db.delete(
            db.get(SessionToken, hashlib.sha256(credentials.credentials.encode()).hexdigest())
        )
        db.commit()

    @app.get("/api/v1/career-goals", response_model=list[GoalOutput])
    def goals(
        db: Db,
        principal: Identity,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: str | None = None,
    ):
        query = select(CareerGoal).where(
            CareerGoal.user_id == principal.user_id, CareerGoal.tenant_id == principal.tenant_id
        )
        if cursor:
            query = query.where(CareerGoal.id > cursor)
        return [
            application.goal_data(g) for g in db.scalars(query.order_by(CareerGoal.id).limit(limit))
        ]

    @app.post("/api/v1/career-goals", response_model=GoalOutput, status_code=201)
    def create_goal(
        payload: GoalInput,
        request: Request,
        db: Db,
        principal: Identity,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
    ):
        result = application.create_goal(
            db, principal, payload.title, idempotency_key, request.state.trace_id
        )
        db.commit()
        return result

    @app.get("/api/v1/career-goals/{goal_id}", response_model=GoalOutput)
    def get_goal(goal_id: str, db: Db, principal: Identity):
        return application.goal_data(application.owned_goal(db, principal, goal_id))

    @app.patch("/api/v1/career-goals/{goal_id}", response_model=GoalOutput)
    def rename(
        goal_id: str,
        payload: GoalInput,
        request: Request,
        db: Db,
        principal: Identity,
        if_match: Annotated[str, Header(pattern=r'^"[1-9][0-9]*"$')],
    ):
        result = application.rename_goal(
            db, principal, goal_id, payload.title, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()
        return result

    register_platform(app, engine, session_dependency, identity)
    register_growth(app, session_dependency, identity)
    register_v1(app, session_dependency, identity)
    register_v2(app, session_dependency, identity)
    from careerpilot.hiring_api import register_hiring

    register_hiring(app, session_dependency, identity)
    from careerpilot.interview_api import register_interviews

    register_interviews(app, session_dependency, identity)
    from careerpilot.source_api import register_sources

    register_sources(app, session_dependency, identity)
    from careerpilot.progress_api import register_progress

    register_progress(app, session_dependency, identity)
    from careerpilot.project_api import register_projects

    register_projects(app, session_dependency, identity)
    from careerpilot.study_api import register_study

    register_study(app, session_dependency, identity)

    from careerpilot.profile_history_api import register_profile_history

    register_profile_history(app, session_dependency, identity)
    from careerpilot.collaboration_api import register_collaboration

    register_collaboration(app, session_dependency, identity)
    from careerpilot.v4_growth_api import register_v4_growth

    register_v4_growth(app, session_dependency, identity)
    from careerpilot.local_tools_api import register_local_tools

    register_local_tools(app, session_dependency, identity)
    if local_only:
        blocked = (
            "/api/v1/auth/",
            "/api/v1/admin/",
            "/api/v1/account/sessions",
            "/api/v1/catalogue/skill/{skill_id}/dependencies",
        )
        app.router.routes[:] = [
            route
            for route in app.router.routes
            if not getattr(route, "path", "").startswith(blocked)
        ]
    from careerpilot.web import mount_web
    from careerpilot.workflow_api import register_workflows

    register_workflows(app, engine, session_dependency, identity)
    from careerpilot.profile_editor import register_profile_editor

    register_profile_editor(app, session_dependency, identity)
    from careerpilot.information_api import register_information

    register_information(app, session_dependency, identity)
    from careerpilot.planning_editor import register_planning_editor

    register_planning_editor(app, session_dependency, identity)
    from careerpilot.business_editor import register_business_editor

    register_business_editor(app, session_dependency, identity)
    mount_web(app)

    @app.get("/", include_in_schema=False)
    def home():
        from fastapi.responses import RedirectResponse

        return RedirectResponse("/app/")

    return app
