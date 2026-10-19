"""V0 API: deliberately no public registration or external actions."""

import hashlib
import time
from contextlib import asynccontextmanager
from typing import Annotated
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

from careerpilot import application
from careerpilot.domain import DomainError, Principal
from careerpilot.persistence import CareerGoal, SessionToken, database_url, make_engine


class GoalInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)


class GoalOutput(GoalInput):
    id: str
    status: str
    version: int
    created_at: str


def create_app(url: str | None = None) -> FastAPI:
    engine = make_engine(url or database_url())

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()

    app = FastAPI(title="CareerPilot", version="0.1.0", lifespan=lifespan)

    def error(request, status, code, message, retryable=False):
        return JSONResponse(
            status_code=status,
            content={
                "code": code,
                "message": message,
                "details": [],
                "trace_id": request.state.trace_id,
                "retryable": retryable,
            },
        )

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = str(uuid4())
        try:
            response = await call_next(request)
        except Exception:
            response = error(request, 500, "INTERNAL_ERROR", "内部错误")
        response.headers["X-Trace-ID"] = request.state.trace_id
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

    def identity(
        db: Db, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
    ):
        if credentials is None:
            raise DomainError("UNAUTHENTICATED", "需要有效登录凭据", 401)
        digest = hashlib.sha256(credentials.credentials.encode()).hexdigest()
        token = db.get(SessionToken, digest)
        if token is None or token.expires_at <= int(time.time()):
            raise DomainError("UNAUTHENTICATED", "需要有效登录凭据", 401)
        return Principal(token.user_id, token.tenant_id)

    Identity = Annotated[Principal, Depends(identity)]

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
                if revision != "0001":
                    raise DomainError("NOT_READY", "数据库迁移未就绪", 503)
        except SQLAlchemyError:
            return error(request, 503, "NOT_READY", "数据库迁移未就绪", True)
        return {"status": "ready", "schema_revision": revision}

    @app.get("/api/v1/me")
    def me(principal: Identity):
        return {"user_id": principal.user_id, "tenant_id": principal.tenant_id}

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

    return app
