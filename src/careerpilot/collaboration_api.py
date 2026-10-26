"""V4 collaboration API: explicit previews, consent and non-cacheable public projections."""

from typing import Annotated, Literal

from fastapi import Depends, Header, Request, Response
from pydantic import Field

from careerpilot import collaboration as service
from careerpilot.growth_api import ConfirmInput, Input


class SpaceInput(ConfirmInput):
    name: str = Field(min_length=1, max_length=100)


class InviteInput(ConfirmInput):
    recipient_id: str = Field(min_length=36, max_length=36)


class TicketInput(ConfirmInput):
    ticket: str = Field(min_length=32, max_length=100)


class ProjectionInput(Input):
    kind: Literal["practice_project", "plan", "project_asset", "project_narrative", "brand_profile"]
    source_id: str = Field(min_length=36, max_length=36)
    fields: list[str] = Field(min_length=1, max_length=10)


class ShareInput(ProjectionInput, ConfirmInput):
    source_version: int = Field(ge=1)
    workspace_id: str | None = Field(default=None, min_length=36, max_length=36)
    public: bool = False
    lifetime_hours: int = Field(default=168, ge=1, le=720)


class CommentInput(ConfirmInput):
    text: str = Field(min_length=1, max_length=2000)


class ModerationInput(ConfirmInput):
    reason: str = Field(min_length=1, max_length=200)


def register_collaboration(app, session_dependency, identity):
    Db, Identity = Depends(session_dependency), Depends(identity)

    @app.get("/api/v1/object-share-sources")
    def share_sources(db=Db, principal=Identity):
        return service.share_sources(db, principal)

    @app.get("/api/v1/workspaces")
    def spaces(db=Db, principal=Identity):
        return {"user_id": principal.user_id, "spaces": service.workspaces(db, principal)}

    @app.post("/api/v1/workspaces", status_code=201)
    def create(body: SpaceInput, request: Request, db=Db, principal=Identity):
        result = service.create_workspace(db, principal, body.name, request.state.trace_id)
        db.commit()
        return result

    @app.post("/api/v1/workspaces/{key}/invitations", status_code=201)
    def invite(key: str, body: InviteInput, request: Request, db=Db, principal=Identity):
        result = service.invite(db, principal, key, body.recipient_id, request.state.trace_id)
        db.commit()
        return result

    @app.post("/api/v1/workspace-invitations/{key}/accept")
    def accept(key: str, body: TicketInput, request: Request, db=Db, principal=Identity):
        result = service.accept(db, principal, key, body.ticket, request.state.trace_id)
        db.commit()
        return result

    @app.delete("/api/v1/workspaces/{key}/members/{member}")
    def remove(key: str, member: str, request: Request, db=Db, principal=Identity):
        result = service.remove_member(db, principal, key, member, request.state.trace_id)
        db.commit()
        return result

    @app.delete("/api/v1/workspaces/{key}")
    def close(key: str, request: Request, db=Db, principal=Identity):
        result = service.close_workspace(db, principal, key, request.state.trace_id)
        db.commit()
        return result

    @app.post("/api/v1/object-shares/preview")
    def preview(body: ProjectionInput, db=Db, principal=Identity):
        return service.projection(db, principal, body.kind, body.source_id, body.fields)

    @app.post("/api/v1/object-shares", status_code=201)
    def share(body: ShareInput, request: Request, db=Db, principal=Identity):
        result = service.share(db, principal, body.model_dump(), request.state.trace_id)
        db.commit()
        return result

    @app.get("/api/v1/object-shares")
    def shares(workspace_id: str | None = None, db=Db, principal=Identity):
        return service.list_shares(db, principal, workspace_id)

    @app.get("/api/v1/object-shares/{key}")
    def detail(key: str, db=Db, principal=Identity):
        return service.share_view(service.visible_share(db, principal, key))

    @app.delete("/api/v1/object-shares/{key}")
    def revoke(key: str, request: Request, db=Db, principal=Identity):
        result = service.revoke(db, principal, key, request.state.trace_id)
        db.commit()
        return result

    @app.get("/api/v1/public-shares/{key}")
    def public(
        key: str,
        response: Response,
        x_share_ticket: Annotated[str, Header(min_length=32, max_length=100)],
        db=Db,
    ):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        row = service.visible_share(db, None, key, x_share_ticket)
        value = service.payload(row)
        return {
            "kind": value["kind"],
            "content": value["content"],
            "notice": "内容由所有者选择分享，不代表独立认证；请勿转发私人信息。",
        }

    @app.get("/api/v1/object-shares/{key}/comments")
    def comments(key: str, db=Db, principal=Identity):
        return service.comments(db, principal, key)

    @app.post("/api/v1/object-shares/{key}/comments", status_code=201)
    def comment(key: str, body: CommentInput, request: Request, db=Db, principal=Identity):
        result = service.comment(db, principal, key, body.text, request.state.trace_id)
        db.commit()
        return result

    @app.post("/api/v1/share-comments/{key}/hide")
    def moderate(key: str, body: ModerationInput, request: Request, db=Db, principal=Identity):
        result = service.moderate(db, principal, key, body.reason, request.state.trace_id)
        db.commit()
        return result
