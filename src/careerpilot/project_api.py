"""Public project API: creation/submission only, never verdict ingestion."""

from typing import Annotated, Literal

from fastapi import Depends, Header, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import ConfigDict, Field, field_validator

from careerpilot import growth, i18n, project_workflow, projects
from careerpilot.growth_api import ConfirmInput, Input


class ProjectInput(ConfirmInput):
    job_id: str = Field(min_length=1, max_length=100)
    template_id: str = Field(min_length=1, max_length=100)


class CodeInput(Input):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    code: str = Field(min_length=1, max_length=32768)

    @field_validator("code")
    @classmethod
    def utf8(cls, value):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("源代码必须为有效 UTF-8 文本") from None
        return value


class MilestoneInput(ConfirmInput):
    status: Literal["pending", "in_progress", "completed"]
    note: str = Field(default="", max_length=4000)
    asset_ids: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("asset_ids")
    @classmethod
    def unique(cls, value):
        if len(value) != len(set(value)) or any(not key or len(key) > 100 for key in value):
            raise ValueError("请选择不重复的成果")
        return value


class AssetInput(Input):
    kind: Literal["text", "github_reference"]
    filename: str = Field(default="reference.md", min_length=1, max_length=110)
    content: str = Field(default="", max_length=65536)
    url: str = Field(default="", max_length=500)


class NarrativeInput(ConfirmInput):
    status: Literal["draft", "confirmed", "withdrawn"]
    situation: str = Field(default="", max_length=2000)
    task: str = Field(default="", max_length=2000)
    action: str = Field(default="", max_length=2000)
    result: str = Field(default="", max_length=2000)
    asset_ids: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("asset_ids")
    @classmethod
    def unique(cls, value):
        return MilestoneInput.unique(value)


class ReviewInput(ConfirmInput):
    decision: Literal["accept", "request_changes"]
    note: str = Field(min_length=1, max_length=4000)


class ProjectPlanInput(ConfirmInput):
    plan_id: str = Field(min_length=1, max_length=100)


def register_projects(app, session_dependency, identity):
    Db, Identity = Depends(session_dependency), Depends(identity)
    Key = Annotated[str, Header(min_length=1, max_length=128)]
    Version = Annotated[str, Header(pattern=r'^"[1-9][0-9]*"$')]
    Revision = Annotated[str, Header(pattern=r'^"[0-9]+"$')]

    @app.get("/api/v1/project-recommendations")
    def recommendations(job_id: str, db=Db, principal=Identity):
        return projects.recommend(db, principal, job_id)

    @app.get("/api/v1/projects")
    def listing(db=Db, principal=Identity):
        return [
            growth.data(r) for r in db.scalars(growth.owner_query(principal, "practice_project"))
        ]

    @app.post("/api/v1/projects", status_code=201)
    def create(
        body: ProjectInput, idempotency_key: Key, request: Request, db=Db, principal=Identity
    ):
        result = projects.create(
            db,
            principal,
            body.model_dump(exclude={"confirmed"}),
            idempotency_key,
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.get("/api/v1/projects/{project_id}")
    def get(project_id: str, db=Db, principal=Identity):
        item = project_workflow.detail(db, principal, project_id)
        submissions = [
            growth.data(r)
            for r in db.scalars(growth.owner_query(principal, "project_submission"))
            if projects.payload(r)["project_id"] == project_id
        ]
        assessments = [
            projects.public_assessment(r)
            for r in db.scalars(growth.owner_query(principal, "project_assessment"))
            if projects.payload(r)["project_id"] == project_id
        ]
        return {**item, "submissions": submissions, "assessments": assessments}

    @app.get("/api/v1/projects/{project_id}/history")
    def history(project_id: str, db=Db, principal=Identity):
        return growth.history(db, principal, project_id, "practice_project")

    @app.patch("/api/v1/projects/{project_id}/milestones/{milestone_id}")
    def milestone(
        project_id: str,
        milestone_id: str,
        body: MilestoneInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = project_workflow.milestone(
            db,
            principal,
            project_id,
            milestone_id,
            body.model_dump(exclude={"confirmed"}),
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.post("/api/v1/projects/{project_id}/assets", status_code=201)
    def asset(
        project_id: str,
        body: AssetInput,
        idempotency_key: Key,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = project_workflow.create_asset(
            db, principal, project_id, body.model_dump(), idempotency_key, request.state.trace_id
        )
        db.commit()
        return result

    @app.get("/api/v1/project-assets/{asset_id}/download")
    def asset_download(asset_id: str, db=Db, principal=Identity):
        from careerpilot.domain import DomainError

        value = growth.data(growth.owned(db, principal, asset_id, "project_asset"))
        if value["status"] != "active" or value["kind"] != "text":
            raise DomainError("ASSET_UNAVAILABLE", "成果文件不可下载", 409)
        return PlainTextResponse(
            value["content"],
            headers={"Content-Disposition": 'attachment; filename="project-artifact.txt"'},
        )

    @app.post("/api/v1/project-assets/{asset_id}/withdraw")
    def withdraw_asset(
        asset_id: str,
        body: ConfirmInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = project_workflow.withdraw_asset(
            db, principal, asset_id, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()
        return result

    @app.put("/api/v1/projects/{project_id}/narrative")
    def narrative(
        project_id: str,
        body: NarrativeInput,
        if_match: Revision,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = project_workflow.save_narrative(
            db,
            principal,
            project_id,
            body.model_dump(exclude={"confirmed"}),
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.get("/api/v1/projects/{project_id}/narrative-preview")
    def narrative_preview(project_id: str, db=Db, principal=Identity):
        return project_workflow.narrative_draft(db, principal, project_id)

    @app.get("/api/v1/projects/{project_id}/narrative/history")
    def narrative_history(project_id: str, db=Db, principal=Identity):
        growth.owned(db, principal, project_id, "practice_project")
        values = project_workflow.items(db, principal, "project_narrative", project_id)
        return growth.history(db, principal, values[0]["id"], "project_narrative") if values else []

    @app.post("/api/v1/projects/{project_id}/narrative-fact", status_code=201)
    def narrative_fact(
        project_id: str,
        body: ConfirmInput,
        if_match: Version,
        idempotency_key: Key,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = project_workflow.confirm_narrative_fact(
            db,
            principal,
            project_id,
            int(if_match.strip('"')),
            idempotency_key,
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.post("/api/v1/projects/{project_id}/reviews", status_code=201)
    def review(
        project_id: str,
        body: ReviewInput,
        if_match: Version,
        idempotency_key: Key,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = project_workflow.review(
            db,
            principal,
            project_id,
            body.decision,
            body.note,
            int(if_match.strip('"')),
            idempotency_key,
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.post("/api/v1/projects/{project_id}/plan-preview", status_code=201)
    def plan_preview(
        project_id: str,
        body: ProjectPlanInput,
        if_match: Version,
        idempotency_key: Key,
        request: Request,
        response: Response,
        db=Db,
        principal=Identity,
    ):
        result = project_workflow.plan_preview(
            db,
            principal,
            project_id,
            body.plan_id,
            int(if_match.strip('"')),
            idempotency_key,
            request.state.trace_id,
        )
        db.commit()
        locale = i18n.negotiate(request.headers.get("accept-language"))
        response.headers.update({"Content-Language": locale, "Vary": "Accept-Language"})
        return i18n.plan_preview(result, locale)

    @app.post("/api/v1/projects/{project_id}/submissions", status_code=201)
    def submit(
        project_id: str,
        body: CodeInput,
        if_match: Version,
        idempotency_key: Key,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = projects.submit(
            db,
            principal,
            project_id,
            body.code,
            int(if_match.strip('"')),
            idempotency_key,
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.post("/api/v1/project-submissions/{artifact_id}/withdraw")
    def withdraw(
        artifact_id: str,
        body: ConfirmInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = projects.withdraw(
            db, principal, artifact_id, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()
        return result

    @app.get("/api/v1/project-submissions/{artifact_id}/statement")
    def statement(artifact_id: str, db=Db, principal=Identity):
        return projects.statement(db, principal, artifact_id)

    @app.post("/api/v1/project-submissions/{artifact_id}/resume-fact", status_code=201)
    def confirm_statement(
        artifact_id: str,
        body: ConfirmInput,
        idempotency_key: Key,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = projects.confirm_statement(
            db, principal, artifact_id, idempotency_key, request.state.trace_id
        )
        db.commit()
        return result
