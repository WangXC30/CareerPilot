"""Reversible organization and typed editing of workflow records."""

import json
from typing import Annotated

from fastapi import Depends, Header, Request
from pydantic import Field
from sqlalchemy import update

from careerpilot import application, application_lab, directions, growth, interviews
from careerpilot.domain import DomainError
from careerpilot.growth_api import ConfirmInput
from careerpilot.local_tools_api import OffersInput
from careerpilot.persistence import CareerGoal, affected
from careerpilot.v4_growth_api import TranslationInput

BUSINESS_KINDS = {
    "interview_session",
    "interview_review",
    "resume_translation",
    "offer_comparison",
    "match_snapshot",
    "plan_preview",
    "review_pipeline",
}
DETAIL_KINDS = {
    "interview_session",
    "match_snapshot",
    "plan_preview",
    "review_pipeline",
    "job_application",
}


class BusinessDetails(ConfirmInput):
    title: str = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=4000)


class ArchiveRecord(ConfirmInput):
    archived: bool


def public_record(row):
    item = growth.data(row)
    return interviews.public(item) if row.kind == "interview_session" else item


def register_business_editor(app, session_dependency, identity):
    Db, Identity = Depends(session_dependency), Depends(identity)
    Version = Annotated[str, Header(pattern=r'^"[1-9][0-9]*"$')]

    @app.put("/api/v1/business-records/{kind}/{record_id}/details")
    def details(
        kind: str,
        record_id: str,
        body: BusinessDetails,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        if kind not in DETAIL_KINDS:
            raise DomainError("NOT_FOUND", "此记录不支持修改说明", 404)
        growth.lock_owner(db, principal)
        row = growth.owned(db, principal, record_id, kind)
        fields = body.model_dump(exclude={"confirmed"})
        growth.scan_upload(growth.dump(fields).encode(), "record-details.txt")
        growth.save(
            db,
            principal,
            kind,
            {**json.loads(row.payload_json), **fields},
            request.state.trace_id,
            row,
            int(if_match.strip('"')),
        )
        result = public_record(row)
        db.commit()
        return result

    @app.post("/api/v1/business-records/{kind}/{record_id}/archive")
    def archive(
        kind: str,
        record_id: str,
        body: ArchiveRecord,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        growth.lock_owner(db, principal)
        expected = int(if_match.strip('"'))
        if kind == "career_goal":
            row = application.owned_goal(db, principal, record_id)
            if row.version != expected:
                raise DomainError("VERSION_CONFLICT", "目标已改变，请刷新", 412)
            if not body.archived and row.status != "archived":
                raise DomainError("INVALID_STATE", "只有移入历史的目标可以恢复", 409)
            if body.archived:
                for record in db.scalars(growth.owner_query(principal, "plan")):
                    payload = json.loads(record.payload_json)
                    if payload.get("goal_id") == record_id and payload.get("status") in {
                        "draft",
                        "active",
                    }:
                        raise DomainError(
                            "INFORMATION_REFERENCED",
                            "仍有学习计划使用此目标，请先处理关联计划",
                            409,
                        )
            result = db.execute(
                update(CareerGoal)
                .where(
                    CareerGoal.id == record_id,
                    CareerGoal.user_id == principal.user_id,
                    CareerGoal.tenant_id == principal.tenant_id,
                    CareerGoal.version == expected,
                )
                .values(status="archived" if body.archived else "draft", version=expected + 1)
            )
            if affected(result) != 1:
                raise DomainError("VERSION_CONFLICT", "目标已改变，请刷新", 412)
            db.expire_all()
            application.audit(
                db,
                principal,
                record_id,
                "goal.archived" if body.archived else "goal.restored",
                expected + 1,
                request.state.trace_id,
            )
            output = application.goal_data(application.owned_goal(db, principal, record_id))
        else:
            if kind not in BUSINESS_KINDS:
                raise DomainError("NOT_FOUND", "此记录不支持移入历史", 404)
            row = growth.owned(db, principal, record_id, kind)
            growth.save(
                db,
                principal,
                kind,
                {**json.loads(row.payload_json), "user_archived": body.archived},
                request.state.trace_id,
                row,
                expected,
            )
            output = public_record(row)
        db.commit()
        return output

    @app.get("/api/v1/career-goals/{goal_id}/history")
    def goal_history(goal_id: str, db=Db, principal=Identity):
        application.owned_goal(db, principal, goal_id)
        row = db.scalar(
            growth.owner_query(principal, "goal_history").where(growth.GrowthRecord.id == goal_id)
        )
        return growth.history(db, principal, goal_id, "goal_history") if row else []

    @app.put("/api/v1/offer-comparisons/{record_id}")
    def revise_offers(
        record_id: str,
        body: OffersInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        growth.lock_owner(db, principal)
        row = growth.owned(db, principal, record_id, "offer_comparison")
        payload = {
            **application_lab.offer_payload(body.model_dump()),
            "user_archived": growth.data(row).get("user_archived", False),
        }
        result = growth.save(
            db, principal, row.kind, payload, request.state.trace_id, row, int(if_match.strip('"'))
        )
        db.commit()
        return result

    @app.put("/api/v1/resume-translations/{record_id}")
    def revise_translation(
        record_id: str,
        body: TranslationInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        growth.lock_owner(db, principal)
        row = growth.owned(db, principal, record_id, "resume_translation")
        payload = directions.translation_payload(
            db, principal, body.model_dump(exclude={"confirmed"})
        )
        payload["user_archived"] = growth.data(row).get("user_archived", False)
        result = growth.save(
            db, principal, row.kind, payload, request.state.trace_id, row, int(if_match.strip('"'))
        )
        db.commit()
        return result
