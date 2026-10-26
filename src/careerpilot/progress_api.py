"""Reports, notification settings, public operations and durable review endpoints."""

from datetime import date
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, Header, Request
from pydantic import Field, field_validator
from sqlalchemy import select

from careerpilot import growth, notifications, operations, reporting, review_pipeline
from careerpilot.growth_api import ConfirmInput
from careerpilot.persistence import AuditEvent
from careerpilot.v1_models import CatalogueEntry


class LogInput(ConfirmInput):
    day: date
    minutes: int = Field(ge=1, le=1440, strict=True)
    note: str = Field(default="", max_length=2000)
    plan_id: str | None = Field(default=None, max_length=36)
    task_id: str | None = Field(default=None, max_length=36)


class NotificationInput(ConfirmInput):
    status: Literal["active", "paused", "cancelled"]
    timezone: str = "Asia/Shanghai"
    hour: int = Field(default=9, ge=0, le=23)
    weekday: int = Field(default=0, ge=0, le=6)
    month_day: int = Field(default=1, ge=1, le=28)
    quiet_start: int = Field(default=22, ge=0, le=23)
    quiet_end: int = Field(default=8, ge=0, le=23)
    daily_limit: int = Field(default=4, ge=1, le=20)
    daily_learning: bool = False
    deadline_check: bool = False
    weekly_report: bool = False
    monthly_report: bool = False
    spaced_review: bool = False
    channel: Literal["in_app"] = "in_app"

    @field_validator("timezone")
    @classmethod
    def zone(cls, value):
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("请选择有效的 IANA 时区") from None
        return value


class PipelineInput(ConfirmInput):
    start: date
    end: date
    plan_id: str = Field(min_length=1, max_length=36)
    query: str = Field(default="", max_length=200)
    reference_url: str = Field(default="", max_length=2000)


class ProposalInput(ConfirmInput):
    public_only_confirmed: Literal[True]
    entry_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,59}$")
    entry_kind: Literal[
        "skill", "career", "resource", "template", "job_category", "matching_policy"
    ]
    base_version: int = Field(ge=0)
    payload: dict
    note: str = Field(min_length=1, max_length=2000)


class ReviewInput(ConfirmInput):
    accepted: bool
    note: str = Field(min_length=1, max_length=2000)


class RollbackInput(ConfirmInput):
    target_version: int = Field(ge=1)
    note: str = Field(min_length=1, max_length=2000)


class TicketInput(ConfirmInput):
    category: Literal["broken_link", "content_correction", "expired_job", "complaint"]
    resource_id: str | None = Field(default=None, max_length=80)
    job_id: str | None = Field(default=None, max_length=36)
    details: str = Field(min_length=1, max_length=4000)


class TicketResponse(ConfirmInput):
    status: Literal["in_progress", "resolved"]
    response: str = Field(min_length=1, max_length=2000)


def register_progress(app, session_dependency, identity):
    Db, Identity = Depends(session_dependency), Depends(identity)
    Version = Annotated[str, Header(pattern=r'^"[0-9]+"$')]
    Key = Annotated[str, Header(min_length=1, max_length=128)]

    def owned_list(db, principal, kind):
        return [
            growth.data(r)
            for r in db.scalars(
                growth.owner_query(principal, kind).order_by(growth.GrowthRecord.created_at.desc())
            )
        ]

    @app.get("/api/v1/progress-report")
    def report(start: date, end: date, db=Db, principal=Identity):
        return reporting.report(db, principal, start.isoformat(), end.isoformat())

    @app.get("/api/v1/study-logs")
    def logs(db=Db, principal=Identity):
        return owned_list(db, principal, "study_log")

    @app.post("/api/v1/study-logs", status_code=201)
    def log(body: LogInput, idempotency_key: Key, request: Request, db=Db, principal=Identity):
        result = reporting.save_log(
            db,
            principal,
            body.model_dump(mode="json", exclude={"confirmed"}),
            idempotency_key,
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.post("/api/v1/study-logs/{record_id}/withdraw")
    def withdraw(
        record_id: str,
        body: ConfirmInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = reporting.withdraw_log(
            db, principal, record_id, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()
        return result

    @app.get("/api/v1/notification-settings")
    def settings(db=Db, principal=Identity):
        return notifications.settings(db, principal)

    @app.put("/api/v1/notification-settings")
    def configure(
        body: NotificationInput, if_match: Version, request: Request, db=Db, principal=Identity
    ):
        result = notifications.save_settings(
            db,
            principal,
            body.model_dump(exclude={"confirmed"}),
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.get("/api/v1/notification-center")
    def notification_center(db=Db, principal=Identity):
        return {
            "items": notifications.listing(db, principal),
            "channels": {"in_app": True, "email": False},
            "notice": "邮件传输未配置，不会发送；各类订阅/提醒分别遵循已确认配置。",
        }

    @app.post("/api/v1/personal-notices/{record_id}/read")
    def read_notice(
        record_id: str,
        body: ConfirmInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = notifications.mark_read(
            db, principal, record_id, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()
        return result

    @app.get("/api/v1/review-pipelines")
    def pipelines(db=Db, principal=Identity):
        return [
            review_pipeline.public(db, principal, r)
            for r in db.scalars(
                growth.owner_query(principal, "review_pipeline").order_by(
                    growth.GrowthRecord.created_at.desc()
                )
            )
        ]

    @app.get("/api/v1/review-pipelines/{record_id}")
    def pipeline(record_id: str, db=Db, principal=Identity):
        return review_pipeline.public(
            db, principal, growth.owned(db, principal, record_id, "review_pipeline")
        )

    @app.post("/api/v1/review-pipelines", status_code=202)
    def create_pipeline(
        body: PipelineInput, idempotency_key: Key, request: Request, db=Db, principal=Identity
    ):
        result = review_pipeline.enqueue(
            db,
            principal,
            body.model_dump(mode="json", exclude={"confirmed"}),
            idempotency_key,
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.get("/api/v1/public-catalogue")
    def public_catalogue(db=Db, principal=Identity):
        return [
            operations.public_data(r)
            for r in db.scalars(
                select(CatalogueEntry)
                .where(CatalogueEntry.kind.in_(operations.SCHEMAS))
                .order_by(CatalogueEntry.id)
            )
        ]

    @app.get("/api/v1/admin/catalogue-proposals")
    def proposals(db=Db, principal=Identity):
        operations.require_role(principal)
        return [
            operations.public_data(r)
            for r in db.scalars(
                select(CatalogueEntry)
                .where(CatalogueEntry.kind == "ops_proposal")
                .order_by(CatalogueEntry.updated_at.desc())
            )
        ]

    @app.post("/api/v1/admin/catalogue-proposals", status_code=201)
    def propose(body: ProposalInput, request: Request, db=Db, principal=Identity):
        result = operations.proposal(
            db,
            principal,
            body.model_dump(exclude={"confirmed", "public_only_confirmed"}),
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.post("/api/v1/admin/catalogue-proposals/{proposal_id}/review")
    def review(
        proposal_id: str,
        body: ReviewInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = operations.review(
            db,
            principal,
            proposal_id,
            int(if_match.strip('"')),
            body.accepted,
            body.note,
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.post("/api/v1/admin/catalogue-proposals/{proposal_id}/publish")
    def publish(
        proposal_id: str,
        body: ConfirmInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = operations.publish(
            db, principal, proposal_id, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()
        return result

    @app.get("/api/v1/admin/catalogue/{entry_id}/history")
    def history(entry_id: str, db=Db, principal=Identity):
        operations.require_role(principal)
        return [
            item
            for r in db.scalars(select(CatalogueEntry).where(CatalogueEntry.kind == "ops_revision"))
            if (item := operations.public_data(r))["entry_id"] == entry_id
        ]

    @app.post("/api/v1/admin/catalogue/{entry_id}/rollback-proposal", status_code=201)
    def rollback(
        entry_id: str,
        body: RollbackInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = operations.rollback_proposal(
            db,
            principal,
            entry_id,
            body.target_version,
            int(if_match.strip('"')),
            body.note,
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.get("/api/v1/support-tickets")
    def tickets(db=Db, principal=Identity):
        return owned_list(db, principal, "support_ticket")

    @app.post("/api/v1/support-tickets", status_code=201)
    def create_ticket(body: TicketInput, request: Request, db=Db, principal=Identity):
        result = operations.create_ticket(
            db, principal, body.model_dump(exclude={"confirmed"}), request.state.trace_id
        )
        db.commit()
        return result

    @app.get("/api/v1/admin/support-queue")
    def queue(db=Db, principal=Identity):
        return operations.ticket_queue(db, principal)

    @app.post("/api/v1/admin/support-queue/{record_id}/respond")
    def respond(
        record_id: str,
        body: TicketResponse,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = operations.respond_ticket(
            db,
            principal,
            record_id,
            int(if_match.strip('"')),
            body.status,
            body.response,
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.get("/api/v1/admin/quality-monitor")
    def monitor(db=Db, principal=Identity):
        return operations.monitor(db, principal)

    @app.get("/api/v1/admin/public-operation-audit")
    def operation_audit(db=Db, principal=Identity):
        operations.require_role(principal)
        return [
            {
                "action": r.action,
                "revision": r.revision,
                "trace_id": r.trace_id,
                "created_at": r.created_at,
            }
            for r in db.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.tenant_id == principal.tenant_id,
                    AuditEvent.action.in_(
                        [
                            "catalogue.proposed",
                            "catalogue.reviewed",
                            "catalogue.published",
                            "support.responded",
                            "access.denied",
                        ]
                    ),
                )
                .order_by(AuditEvent.created_at.desc())
                .limit(200)
            )
        ]
