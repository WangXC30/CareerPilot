"""Timeline and GitHub account binding endpoints."""

import json
from typing import Annotated

from fastapi import Depends, Query, Request
from pydantic import Field

from careerpilot import github_binding, timeline
from careerpilot.domain import DomainError
from careerpilot.growth_api import ConfirmInput


class CallbackInput(ConfirmInput):
    state: str = Field(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    code: str = Field(min_length=1, max_length=512, pattern=r"^[A-Za-z0-9_-]+$")


def register_profile_history(app, session_dependency, identity):
    Db, Identity = Depends(session_dependency), Depends(identity)

    @app.get("/api/v1/profile-timeline")
    def history(
        cursor: Annotated[str | None, Query(max_length=300)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
        db=Db,
        principal=Identity,
    ):
        before = None
        if cursor:
            try:
                before = json.loads(cursor)
                if (
                    not isinstance(before, list)
                    or len(before) != 2
                    or not all(isinstance(x, str) for x in before)
                ):
                    raise ValueError()
            except ValueError:
                raise DomainError("INVALID_CURSOR", "时间线分页标记无效", 422) from None
        return timeline.events(db, principal, before, limit)

    @app.get("/api/v1/github-binding")
    def binding(db=Db, principal=Identity):
        return github_binding.status(db, principal)

    @app.post("/api/v1/github-binding/start")
    def start(body: ConfirmInput, request: Request, db=Db, principal=Identity):
        result = github_binding.start(db, principal, request.state.trace_id)
        db.commit()
        return result

    @app.post("/api/v1/github-binding/finish")
    def finish(body: CallbackInput, request: Request, db=Db, principal=Identity):
        result = github_binding.finish(db, principal, body.state, body.code, request.state.trace_id)
        db.commit()
        return result

    @app.post("/api/v1/github-binding/sync")
    def sync(body: ConfirmInput, request: Request, db=Db, principal=Identity):
        result = github_binding.sync(db, principal, request.state.trace_id)
        db.commit()
        return result

    @app.post("/api/v1/github-binding/unbind")
    def unbind(body: ConfirmInput, request: Request, db=Db, principal=Identity):
        result = github_binding.unbind(db, principal, request.state.trace_id)
        db.commit()
        return result
