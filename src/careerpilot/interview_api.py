"""Owned text-practice sessions, evidence-limited evaluations and review previews."""

from typing import Annotated, Literal

from fastapi import Depends, Header, Request, Response
from pydantic import Field, field_validator

from careerpilot import growth, i18n, interviews
from careerpilot.growth_api import ConfirmInput, Input


class SessionInput(ConfirmInput):
    resume_version_id: str = Field(min_length=1, max_length=100)
    job_id: str = Field(min_length=1, max_length=100)
    fact_ids: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("fact_ids")
    @classmethod
    def unique(cls, value):
        if len(set(value)) != len(value) or any(not key or len(key) > 100 for key in value):
            raise ValueError("请选择不重复的项目陈述")
        return value


class ScheduleInput(Input):
    version: int = Field(ge=1)
    date: str = Field(min_length=10, max_length=10)
    time: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: str = Field(min_length=1, max_length=100)
    preparation_note: str = Field(default="", max_length=2000)


class ScheduleConfirmInput(ScheduleInput):
    preview_hash: str = Field(min_length=64, max_length=64)
    confirmed: Literal[True]


class AnswerInput(Input):
    question_id: str = Field(min_length=1, max_length=100)
    text: str = Field(default="", max_length=6000)
    choice: int | None = Field(default=None, ge=0, le=2, strict=True)
    skipped: bool = False


class DecisionInput(Input):
    question_id: str = Field(min_length=1, max_length=100)
    skill_id: str = Field(min_length=1, max_length=80)
    minutes: int = Field(ge=10, le=180, strict=True)
    note: str = Field(default="", max_length=2000)


class ReviewInput(ConfirmInput):
    status: Literal["confirmed", "withdrawn"]
    summary: str = Field(min_length=1, max_length=4000)
    decisions: list[DecisionInput] = Field(default_factory=list, max_length=12)

    @field_validator("decisions")
    @classmethod
    def unique(cls, value):
        if len({(d.question_id, d.skill_id) for d in value}) != len(value):
            raise ValueError("复习项重复")
        return value


class PreviewInput(ConfirmInput):
    plan_id: str = Field(min_length=1, max_length=100)


class SelfReviewDecision(Input):
    skill_id: str = Field(min_length=1, max_length=80)
    minutes: int = Field(ge=10, le=180, strict=True)
    note: str = Field(default="", max_length=2000)


class SelfReviewInput(ConfirmInput):
    status: Literal["confirmed", "withdrawn"]
    summary: str = Field(min_length=1, max_length=4000)
    application_id: str | None = Field(default=None, min_length=1, max_length=36)
    decisions: list[SelfReviewDecision] = Field(default_factory=list, max_length=12)

    @field_validator("decisions")
    @classmethod
    def unique(cls, value):
        if len({item.skill_id for item in value}) != len(value):
            raise ValueError("复习技能重复")
        return value


def register_interviews(app, session_dependency, identity):
    Db, Identity = Depends(session_dependency), Depends(identity)
    Version = Annotated[str, Header(pattern=r'^"[0-9]+"$')]
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

    @app.post("/api/v1/applications/{application_id}/interview-preview")
    def schedule_preview(application_id: str, body: ScheduleInput, db=Db, principal=Identity):
        from careerpilot import chat_followup

        return chat_followup.proposal(
            db,
            principal,
            "record_interview",
            {"application_id": application_id, **body.model_dump()},
        )

    @app.post("/api/v1/applications/{application_id}/interview")
    def schedule_interview(
        application_id: str,
        body: ScheduleConfirmInput,
        idempotency_key: Key,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        from careerpilot import chat_followup
        from careerpilot.domain import DomainError

        args = {
            "application_id": application_id,
            **body.model_dump(exclude={"preview_hash", "confirmed"}),
        }
        proposed = chat_followup.proposal(db, principal, "record_interview", args)
        if proposed["context_hash"] != body.preview_hash:
            raise DomainError("STALE_PREVIEW", "申请或面试安排已改变，请重新预览。", 412)
        result = chat_followup.execute(
            db,
            principal,
            "record_interview",
            args,
            proposed,
            request.state.trace_id,
            idempotency_key,
        )
        db.commit()
        return result

    @app.get("/api/v1/interviews")
    def sessions(db=Db, principal=Identity):
        return [interviews.public(r) for r in listing(db, principal, "interview_session")]

    @app.post("/api/v1/interviews", status_code=201)
    def create(
        body: SessionInput, idempotency_key: Key, request: Request, db=Db, principal=Identity
    ):
        result = interviews.create(
            db,
            principal,
            body.model_dump(exclude={"confirmed"}),
            idempotency_key,
            request.state.trace_id,
        )
        db.commit()
        return interviews.public(result)

    @app.get("/api/v1/interviews/{session_id}")
    def get(session_id: str, db=Db, principal=Identity):
        return interviews.public(
            growth.data(growth.owned(db, principal, session_id, "interview_session"))
        )

    @app.get("/api/v1/interviews/{session_id}/history")
    def history(session_id: str, db=Db, principal=Identity):
        # Raw aggregate history contains hidden rubric fields; return the same safe representation.
        return [
            {
                "version": r["version"],
                "created_at": r["created_at"],
                "session": interviews.public(
                    {
                        "id": session_id,
                        "version": r["version"],
                        "created_at": r["created_at"],
                        **r["payload"],
                    }
                ),
            }
            for r in growth.history(db, principal, session_id, "interview_session")
        ]

    @app.post("/api/v1/interviews/{session_id}/answers")
    def answer(
        session_id: str,
        body: AnswerInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = interviews.answer(
            db,
            principal,
            session_id,
            body.model_dump(),
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return interviews.public(result)

    @app.post("/api/v1/interviews/{session_id}/abandon")
    def abandon(
        session_id: str,
        body: ConfirmInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = interviews.abandon(
            db, principal, session_id, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()
        return interviews.public(result)

    @app.post("/api/v1/interviews/{session_id}/evaluate", status_code=201)
    def evaluate(
        session_id: str,
        body: ConfirmInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = interviews.evaluate(
            db, principal, session_id, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()
        return result

    @app.get("/api/v1/interview-evaluations/{evaluation_id}")
    def evaluation(evaluation_id: str, db=Db, principal=Identity):
        return growth.data(growth.owned(db, principal, evaluation_id, "interview_evaluation"))

    @app.get("/api/v1/interview-reviews")
    def reviews(db=Db, principal=Identity):
        return listing(db, principal, "interview_review")

    @app.post("/api/v1/interview-reviews/self-report", status_code=201)
    def create_self_review(
        body: SelfReviewInput, idempotency_key: Key, request: Request, db=Db, principal=Identity
    ):
        result = interviews.save_self_review(
            db,
            principal,
            body.model_dump(exclude={"confirmed"}),
            request.state.trace_id,
            key=idempotency_key,
        )
        db.commit()
        return result

    @app.put("/api/v1/interview-reviews/{review_id}/self-report")
    def edit_self_review(
        review_id: str,
        body: SelfReviewInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = interviews.save_self_review(
            db,
            principal,
            body.model_dump(exclude={"confirmed"}),
            request.state.trace_id,
            review_id=review_id,
            expected=int(if_match.strip('"')),
        )
        db.commit()
        return result

    @app.put("/api/v1/interview-evaluations/{evaluation_id}/review")
    def review(
        evaluation_id: str,
        body: ReviewInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = interviews.save_review(
            db,
            principal,
            evaluation_id,
            body.model_dump(exclude={"confirmed"}),
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.post("/api/v1/interview-reviews/{review_id}/plan-preview", status_code=201)
    def preview(
        review_id: str,
        body: PreviewInput,
        idempotency_key: Key,
        if_match: Version,
        request: Request,
        response: Response,
        db=Db,
        principal=Identity,
    ):
        result = interviews.plan_preview(
            db,
            principal,
            review_id,
            body.plan_id,
            int(if_match.strip('"')),
            idempotency_key,
            request.state.trace_id,
        )
        db.commit()
        locale = i18n.negotiate(request.headers.get("accept-language"))
        response.headers.update({"Content-Language": locale, "Vary": "Accept-Language"})
        return i18n.plan_preview(result, locale)
