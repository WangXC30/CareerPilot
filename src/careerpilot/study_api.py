"""Study modes, personal resource feedback and review queue HTTP contracts."""

from typing import Annotated, Literal

from fastapi import Depends, Header, Request, Response
from pydantic import Field

from careerpilot import i18n, learning_resources, study
from careerpilot.growth_api import ConfirmInput


class ModeInput(ConfirmInput):
    mode: Literal["sprint", "balanced", "research_busy", "light"]


class ResourceInput(ConfirmInput):
    favorite: bool = False
    quality: Literal["unrated", "helpful", "poor"] = "unrated"
    broken: bool = False
    note: str = Field(default="", max_length=1000)


def register_study(app, session_dependency, identity):
    Db, Identity = Depends(session_dependency), Depends(identity)
    Version = Annotated[str, Header(pattern=r'^"[0-9]+"$')]
    Key = Annotated[str, Header(min_length=1, max_length=128)]

    @app.get("/api/v1/learning-resources")
    def resources(db=Db, principal=Identity):
        return learning_resources.entries(db, principal)

    @app.post("/api/v1/plans/{plan_id}/mode-preview", status_code=201)
    def mode(
        plan_id: str,
        body: ModeInput,
        request: Request,
        response: Response,
        if_match: Version,
        idempotency_key: Key,
        db=Db,
        principal=Identity,
    ):
        result = study.mode_preview(
            db,
            principal,
            plan_id,
            body.mode,
            int(if_match.strip('"')),
            idempotency_key,
            request.state.trace_id,
        )
        db.commit()
        locale = i18n.negotiate(request.headers.get("accept-language"))
        response.headers.update({"Content-Language": locale, "Vary": "Accept-Language"})
        return i18n.plan_preview(result, locale)

    @app.get("/api/v1/study-reviews")
    def reviews(db=Db, principal=Identity):
        return study.review_queue(db, principal)

    @app.put("/api/v1/learning-resources/{resource_id}/feedback")
    def resource_feedback(
        resource_id: str,
        body: ResourceInput,
        request: Request,
        if_match: Version,
        db=Db,
        principal=Identity,
    ):
        result = learning_resources.feedback(
            db,
            principal,
            resource_id,
            body.model_dump(exclude={"confirmed"}),
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return result
