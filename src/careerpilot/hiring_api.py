"""Private resume, application and reminder HTTP contracts."""

from datetime import datetime
from hashlib import sha256
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, Header, Query, Request
from fastapi.responses import Response
from pydantic import Field, field_validator, model_validator

from careerpilot import applications, growth, hiring_reminders, resume_exports, resumes
from careerpilot.domain import DomainError
from careerpilot.growth_api import ClaimInput, Input

FactKind = Literal["name", "contact", "summary", "education", "experience", "project", "skill"]


class ImportClaim(ClaimInput):
    kind: FactKind
    category: Literal["self_report", "document_fact"] = "self_report"


class ImportConfirmation(Input):
    confirmed: Literal[True]
    claims: list[ImportClaim] = Field(min_length=1, max_length=100)


class FactInput(Input):
    kind: FactKind
    source_type: Literal["manual", "memory", "profile"]
    text: str | None = Field(default=None, min_length=1, max_length=4000)
    source_id: str | None = Field(default=None, min_length=1, max_length=100)
    source_version: int | None = Field(default=None, ge=1)
    profile_field: Literal["major", "education", "graduation_date"] | None = None
    confirmed: Literal[True]

    @model_validator(mode="after")
    def source_contract(self):
        if self.source_type == "manual":
            valid = self.text is not None and all(
                v is None for v in (self.source_id, self.source_version, self.profile_field)
            )
        elif self.source_type == "memory":
            valid = bool(
                self.source_id
                and self.source_version
                and self.text is None
                and self.profile_field is None
            )
        else:
            valid = bool(
                self.source_version
                and self.profile_field
                and self.source_id is None
                and self.text is None
                and self.kind == "education"
            )
        if not valid:
            raise ValueError("来源字段不完整或含冲突字段")
        return self


class ReasonInput(Input):
    reason: str = Field(min_length=1, max_length=1000)
    confirmed: Literal[True]


class ResumeInput(Input):
    fact_ids: list[str] = Field(min_length=1, max_length=100)
    job_id: str | None = Field(default=None, min_length=1, max_length=100)
    confirmed: Literal[True]

    @field_validator("fact_ids")
    @classmethod
    def unique_facts(cls, value):
        if len(set(value)) != len(value) or any(not v or len(v) > 100 for v in value):
            raise ValueError("事实编号需唯一且有效")
        return value


class ResumeCreate(ResumeInput):
    title: str = Field(min_length=1, max_length=100)


class RestoreInput(Input):
    version_id: str = Field(min_length=1, max_length=100)
    confirmed: Literal[True]


class TailorInput(Input):
    job_id: str = Field(min_length=1, max_length=100)
    confirmed: Literal[True]


class DraftInput(Input):
    kind: Literal["cover_letter", "email", "introduction"]
    tone: Literal["plain", "formal"]
    confirmed: Literal[True]


class ApplicationInput(Input):
    job_id: str = Field(min_length=1, max_length=100)
    resume_version_id: str = Field(min_length=1, max_length=100)
    confirmed: Literal[True]


class Checklist(Input):
    facts: Literal[True]
    contact: Literal[True]
    requirements: Literal[True]
    privacy: Literal[True]
    source_reviewed: Literal[True]


class TransitionInput(Input):
    target: Literal[
        "prepared",
        "awaiting_user_submission",
        "assessment",
        "interviewing",
        "offer",
        "rejected",
        "withdrawn",
        "no_response",
    ]
    note: str = Field(default="", max_length=2000)
    checklist: Checklist | None = None
    confirmed: Literal[True]


class ReceiptInput(Input):
    material_id: str | None = Field(default=None, min_length=1, max_length=100)
    material_version: int | None = Field(default=None, ge=1)
    receipt_code: str | None = Field(default=None, min_length=3, max_length=100)
    submitted_at: datetime | None = None
    note: str = Field(default="", max_length=2000)
    confirmed: Literal[True]

    @model_validator(mode="after")
    def receipt_contract(self):
        values = [self.material_version, self.receipt_code, self.submitted_at]
        if self.material_id:
            if (
                any(v is None for v in values)
                or self.submitted_at is None
                or self.submitted_at.tzinfo is None
            ):
                raise ValueError("回执需包含材料版本、编号及带时区的提交时间")
        elif any(v is not None for v in values):
            raise ValueError("未选择回执材料")
        return self


class ReminderInput(Input):
    kind: Literal["deadline", "assessment", "interview", "follow_up"]
    local_time: str = Field(min_length=16, max_length=19)
    timezone: str = Field(max_length=100)
    quiet_start: int = Field(default=22, ge=0, le=23)
    quiet_end: int = Field(default=8, ge=0, le=23)
    confirmed: Literal[True]

    @field_validator("local_time")
    @classmethod
    def naive_time(cls, value):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            raise ValueError("请使用不带偏移的本地时间，并单独指定时区")
        return parsed.isoformat(timespec="seconds")

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Invalid IANA timezone") from None
        return value


class ReminderUpdate(ReminderInput):
    status: Literal["active", "paused", "cancelled"]


def register_hiring(app, session_dependency, identity):
    Db, Identity = Depends(session_dependency), Depends(identity)
    Version = Annotated[str, Header(pattern=r'^"[1-9][0-9]*"$')]
    Key = Annotated[str, Header(min_length=1, max_length=128)]

    def listing(db, principal, kind):
        return [
            growth.data(r)
            for r in db.scalars(
                growth.owner_query(principal, kind).order_by(
                    growth.GrowthRecord.created_at.desc(), growth.GrowthRecord.id
                )
            )
        ]

    @app.post("/api/v1/resume-imports", status_code=201)
    async def import_resume(
        request: Request,
        filename: Annotated[str, Query(min_length=1, max_length=180)],
        db=Db,
        principal=Identity,
    ):
        if any(c in filename for c in ("/", "\\", "\x00")):
            raise DomainError("UPLOAD_REJECTED", "文件名无效", 422)
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 1024 * 1024:
                raise DomainError("UPLOAD_REJECTED", "文件超过 1MiB", 422)
        result = resumes.import_preview(
            db, principal, filename, bytes(content), request.state.trace_id
        )
        db.commit()
        return result

    @app.post("/api/v1/resume-imports/{material_id}/confirm", status_code=201)
    def confirm_import(
        material_id: str,
        body: ImportConfirmation,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = resumes.confirm_import(
            db,
            principal,
            material_id,
            [claim.model_dump() for claim in body.claims],
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.get("/api/v1/resume-facts")
    def facts(db=Db, principal=Identity):
        return [resumes.fact(db, principal, r["id"]) for r in listing(db, principal, "resume_fact")]

    @app.post("/api/v1/resume-facts", status_code=201)
    def add_fact(body: FactInput, request: Request, db=Db, principal=Identity):
        result = resumes.create_fact(
            db, principal, body.model_dump(exclude={"confirmed"}), request.state.trace_id
        )
        db.commit()
        return result

    @app.post("/api/v1/resume-facts/{fact_id}/revoke")
    def revoke(fact_id: str, body: ReasonInput, request: Request, db=Db, principal=Identity):
        result = resumes.revoke_fact(db, principal, fact_id, body.reason, request.state.trace_id)
        db.commit()
        return result

    @app.get("/api/v1/resumes")
    def roots(db=Db, principal=Identity):
        return listing(db, principal, "resume")

    @app.post("/api/v1/resumes", status_code=201)
    def create(
        body: ResumeCreate, idempotency_key: Key, request: Request, db=Db, principal=Identity
    ):
        result = resumes.create_resume(
            db,
            principal,
            body.title,
            body.fact_ids,
            body.job_id,
            idempotency_key,
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.get("/api/v1/resumes/{resume_id}/versions")
    def versions(resume_id: str, db=Db, principal=Identity):
        growth.owned(db, principal, resume_id, "resume")
        return sorted(
            [v for v in listing(db, principal, "resume_version") if v["resume_id"] == resume_id],
            key=lambda v: v["sequence"],
            reverse=True,
        )

    @app.post("/api/v1/resumes/{resume_id}/versions", status_code=201)
    def revise(
        resume_id: str,
        body: ResumeInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = resumes.revise(
            db,
            principal,
            resume_id,
            body.fact_ids,
            body.job_id,
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.post("/api/v1/resumes/{resume_id}/restore", status_code=201)
    def restore(
        resume_id: str,
        body: RestoreInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = resumes.restore(
            db,
            principal,
            resume_id,
            body.version_id,
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.post("/api/v1/resumes/{resume_id}/tailor", status_code=201)
    def tailor(
        resume_id: str,
        body: TailorInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = resumes.tailor(
            db, principal, resume_id, body.job_id, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()
        return result

    @app.get("/api/v1/resume-versions/{version_id}")
    def get_version(version_id: str, db=Db, principal=Identity):
        result = resumes.owned_version(db, principal, version_id)
        return {**result, "source_status": resumes.version_status(db, principal, result)}

    @app.get("/api/v1/resume-versions/{version_id}/ats")
    def ats(version_id: str, db=Db, principal=Identity):
        return resumes.ats(db, principal, resumes.owned_version(db, principal, version_id))

    @app.post("/api/v1/resume-versions/{version_id}/drafts", status_code=201)
    def draft(version_id: str, body: DraftInput, request: Request, db=Db, principal=Identity):
        result = resumes.material_draft(
            db,
            principal,
            resumes.owned_version(db, principal, version_id),
            body.kind,
            body.tone,
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.get("/api/v1/application-drafts")
    def drafts(db=Db, principal=Identity):
        return listing(db, principal, "application_draft")

    @app.post("/api/v1/resume-versions/{version_id}/export/{output_format}")
    def export(
        version_id: str,
        output_format: Literal["docx", "pdf"],
        request: Request,
        db=Db,
        principal=Identity,
    ):
        # Serialize export against source changes and withdrawal.
        growth.lock_owner(db, principal)
        result = resumes.owned_version(db, principal, version_id)
        resumes.valid_facts(db, principal, [f["id"] for f in result["facts"]])
        content = (
            resume_exports.docx(result) if output_format == "docx" else resume_exports.pdf(result)
        )
        from careerpilot.application import audit

        audit(
            db, principal, version_id, f"resume.export.{output_format}", 1, request.state.trace_id
        )
        db.commit()
        job = result["job"]["id"][:8] if result["job"] else "general"
        filename = f"resume-{result['resume_id'][:8]}-v{result['sequence']}-{job}.{output_format}"
        media = (
            "application/pdf"
            if output_format == "pdf"
            else ("application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        )
        return Response(
            content,
            media_type=media,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
                "X-Resume-Version": version_id,
                "X-Resume-Content-Hash": result["content_hash"],
                "X-Content-SHA256": sha256(content).hexdigest(),
            },
        )

    @app.get("/api/v1/applications")
    def board(db=Db, principal=Identity):
        return listing(db, principal, "job_application")

    @app.post("/api/v1/applications", status_code=201)
    def apply(
        body: ApplicationInput, idempotency_key: Key, request: Request, db=Db, principal=Identity
    ):
        result = applications.create(
            db,
            principal,
            body.job_id,
            body.resume_version_id,
            idempotency_key,
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.get("/api/v1/applications/{application_id}/history")
    def history(application_id: str, db=Db, principal=Identity):
        return growth.history(db, principal, application_id, "job_application")

    @app.post("/api/v1/applications/{application_id}/transition")
    def transition(
        application_id: str,
        body: TransitionInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = applications.change(
            db,
            principal,
            application_id,
            body.target,
            int(if_match.strip('"')),
            body.note,
            request.state.trace_id,
            body.checklist.model_dump() if body.checklist else None,
        )
        db.commit()
        return result

    @app.post("/api/v1/applications/{application_id}/receipt")
    def receipt(
        application_id: str,
        body: ReceiptInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = applications.receipt(
            db,
            principal,
            application_id,
            body.model_dump(mode="json"),
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.get("/api/v1/application-reminders")
    def reminders(db=Db, principal=Identity):
        return listing(db, principal, "application_reminder")

    @app.post("/api/v1/applications/{application_id}/reminders", status_code=201)
    def remind(
        application_id: str,
        body: ReminderInput,
        idempotency_key: Key,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = hiring_reminders.create(
            db,
            principal,
            application_id,
            body.model_dump(exclude={"confirmed"}),
            idempotency_key,
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.put("/api/v1/application-reminders/{reminder_id}")
    def configure(
        reminder_id: str,
        body: ReminderUpdate,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = hiring_reminders.configure(
            db,
            principal,
            reminder_id,
            body.model_dump(exclude={"confirmed"}),
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.get("/api/v1/application-notifications")
    def notices(db=Db, principal=Identity):
        return listing(db, principal, "application_notice")

    @app.post("/api/v1/application-notifications/{notice_id}/read")
    def read(notice_id: str, if_match: Version, request: Request, db=Db, principal=Identity):
        row = growth.owned(db, principal, notice_id, "application_notice")
        result = growth.save(
            db,
            principal,
            "application_notice",
            {**applications.payload(growth.data(row)), "read": True},
            request.state.trace_id,
            row,
            int(if_match.strip('"')),
        )
        db.commit()
        return result
