"""HTTP adapters for the first V1 business slice."""

import json
from datetime import date
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, Header, Query, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete

from careerpilot import growth, i18n, learning, market
from careerpilot.application import audit
from careerpilot.domain import DomainError
from careerpilot.growth_models import GrowthRecord, GrowthRevision
from careerpilot.safety import scan_upload


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProfileInput(Input):
    education: Literal["high_school", "associate", "bachelor", "master", "doctor", "other"]
    major: str = Field(min_length=1, max_length=100)
    academic_year: str = Field(min_length=1, max_length=40)
    graduation_date: date
    preferred_cities: list[str] = Field(min_length=1, max_length=20)
    expected_monthly_salary: int | None = Field(default=None, ge=0, le=1000000)
    weekly_minutes: int = Field(ge=0, le=10080)
    preferences: str = Field(default="", max_length=2000)
    timezone: str = "Asia/Shanghai"

    @field_validator("preferred_cities")
    @classmethod
    def cities(cls, value):
        if any(not city.strip() or len(city) > 100 for city in value):
            raise ValueError("Invalid cities")
        return list(dict.fromkeys(city.strip() for city in value))

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Invalid IANA timezone") from None
        return value


class ConfirmProfile(ProfileInput):
    confirmed: Literal[True]


class RestoreInput(Input):
    target_version: int = Field(ge=1)
    confirmed: Literal[True]


class ClaimInput(Input):
    text: str = Field(min_length=1, max_length=2000)
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    category: Literal["self_report", "document_fact", "inference"] = "self_report"


class MaterialConfirmation(Input):
    confirmed: Literal[True]
    claims: list[ClaimInput] = Field(min_length=1, max_length=100)


class MemoryEdit(Input):
    text: str = Field(min_length=1, max_length=2000)
    category: Literal["self_report", "document_fact", "inference"]
    confirmed: Literal[True]


class JobInput(Input):
    content: str = Field(min_length=1, max_length=100000)
    source_url: str | None = Field(default=None, max_length=2000)


class ConfirmInput(Input):
    confirmed: Literal[True]


def register_growth(app, session_dependency, identity):
    Db, Identity = Depends(session_dependency), Depends(identity)
    Version = Annotated[str, Header(pattern=r'^"[0-9]+"$')]

    @app.get("/api/v1/profile")
    def get_profile(db=Db, principal=Identity):
        row = growth.profile(db, principal)
        return growth.data(row) if row else {"version": 0, "profile": None}

    @app.post("/api/v1/profile/preview")
    def preview_profile(
        body: ProfileInput, request: Request, response: Response, db=Db, principal=Identity
    ):
        result = growth.profile_impact(db, principal, body.model_dump(mode="json"))
        locale = i18n.negotiate(request.headers.get("accept-language"))
        response.headers.update({"Content-Language": locale, "Vary": "Accept-Language"})
        if locale == "en-US":
            result["message"] = i18n.GROWTH_MESSAGES_EN["profile_preview"]
        return result

    @app.put("/api/v1/profile")
    def put_profile(
        body: ConfirmProfile, if_match: Version, request: Request, db=Db, principal=Identity
    ):
        payload = body.model_dump(mode="json", exclude={"confirmed"})
        scan_upload(growth.dump(payload).encode(), "profile.json")
        result = growth.save_profile(
            db, principal, payload, int(if_match.strip('"')), request.state.trace_id
        )
        result["replan_preview"] = learning.auto_preview(
            db, principal, "ProfileChanged", request.state.trace_id
        )
        db.commit()
        return result

    @app.get("/api/v1/profile/history")
    def profile_history(db=Db, principal=Identity):
        row = growth.profile(db, principal)
        return growth.history(db, principal, row.id, "profile") if row else []

    @app.post("/api/v1/profile/restore")
    def restore(body: RestoreInput, if_match: Version, request: Request, db=Db, principal=Identity):
        result = growth.restore_profile(
            db, principal, body.target_version, int(if_match.strip('"')), request.state.trace_id
        )
        result["replan_preview"] = learning.auto_preview(
            db, principal, "ProfileChanged", request.state.trace_id
        )
        db.commit()
        return result

    @app.post("/api/v1/materials", status_code=201)
    async def upload_material(
        request: Request,
        filename: Annotated[str, Query(min_length=1, max_length=180)],
        db=Db,
        principal=Identity,
    ):
        if any(char in filename for char in ("/", "\\", "\x00")):
            raise DomainError("UPLOAD_REJECTED", "文件名无效", 422)
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 1024 * 1024:
                raise DomainError("UPLOAD_REJECTED", "文件超过 1MiB", 422)
        result = growth.import_material(
            db, principal, filename, bytes(content), request.state.trace_id
        )
        db.commit()
        return result

    @app.get("/api/v1/materials")
    def materials(db=Db, principal=Identity):
        return [
            {k: v for k, v in growth.data(row).items() if k not in {"content", "candidates"}}
            for row in db.scalars(
                growth.owner_query(principal, "material").order_by(GrowthRecord.created_at.desc())
            )
        ]

    @app.get("/api/v1/materials/{material_id}")
    def get_material(material_id: str, db=Db, principal=Identity):
        return growth.data(growth.owned(db, principal, material_id, "material"))

    @app.get("/api/v1/materials/{material_id}/original", response_class=PlainTextResponse)
    def original(material_id: str, db=Db, principal=Identity):
        return json.loads(growth.owned(db, principal, material_id, "material").payload_json)[
            "content"
        ]

    @app.post("/api/v1/materials/{material_id}/confirm")
    def confirm_material(
        material_id: str,
        body: MaterialConfirmation,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        claims = [c.model_dump() for c in body.claims]
        scan_upload(growth.dump(claims).encode(), "claims.json")
        result = growth.confirm_material(
            db, principal, material_id, claims, int(if_match.strip('"')), request.state.trace_id
        )
        db.commit()
        return result

    @app.get("/api/v1/memories")
    def memories(db=Db, principal=Identity):
        return [
            growth.data(row)
            for row in db.scalars(
                growth.owner_query(principal, "memory").order_by(GrowthRecord.created_at.desc())
            )
        ]

    @app.patch("/api/v1/memories/{memory_id}")
    def edit_memory(
        memory_id: str,
        body: MemoryEdit,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        scan_upload(body.text.encode(), "memory.txt")
        result = growth.update_memory(
            db,
            principal,
            memory_id,
            body.text,
            body.category,
            int(if_match.strip('"')),
            request.state.trace_id,
        )
        db.commit()
        return result

    @app.delete("/api/v1/memories/{memory_id}", status_code=204)
    def delete_memory(
        memory_id: str, if_match: Version, request: Request, db=Db, principal=Identity
    ):
        growth.lock_owner(db, principal)
        row = growth.owned(db, principal, memory_id, "memory")
        version = int(if_match.strip('"'))
        # A conditional UPDATE acquires the write lock before removing history.
        growth.save(
            db,
            principal,
            "memory",
            json.loads(row.payload_json),
            request.state.trace_id,
            row,
            version,
        )
        db.flush()
        db.execute(delete(GrowthRevision).where(GrowthRevision.record_id == row.id))
        db.delete(row)
        audit(db, principal, memory_id, "memory.deleted", version + 1, request.state.trace_id)
        db.commit()

    @app.post("/api/v1/jobs/import", status_code=201)
    def import_job(body: JobInput, request: Request, db=Db, principal=Identity):
        result = growth.import_job(
            db, principal, body.content, body.source_url, request.state.trace_id
        )
        db.commit()
        return result

    @app.get("/api/v1/jobs")
    def jobs(
        q: Annotated[str, Query(max_length=200)] = "",
        location: Annotated[str, Query(max_length=100)] = "",
        company: Annotated[str, Query(max_length=100)] = "",
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: str | None = None,
        db=Db,
        principal=Identity,
    ):
        return market.search(
            db, principal, {"q": q, "location": location, "company": company}, limit, cursor
        )

    @app.get("/api/v1/jobs/{job_id}")
    def job(job_id: str, db=Db, principal=Identity):
        return growth.data(growth.owned(db, principal, job_id, "job"))

    @app.get("/api/v1/jobs/{job_id}/history")
    def job_history(job_id: str, db=Db, principal=Identity):
        return growth.history(db, principal, job_id, "job")

    @app.post("/api/v1/career-goals/{goal_id}/preview")
    def goal_preview(goal_id: str, request: Request, response: Response, db=Db, principal=Identity):
        result = growth.preview_goal(db, principal, goal_id, request.state.trace_id)
        db.commit()
        locale = i18n.negotiate(request.headers.get("accept-language"))
        response.headers.update({"Content-Language": locale, "Vary": "Accept-Language"})
        if locale == "en-US":
            result["impact"] = i18n.GROWTH_MESSAGES_EN["goal_preview"]
            if result["plan_impact"]:
                plan = result["plan_impact"]
                result["impact"] += i18n.GROWTH_MESSAGES_EN["goal_plan"].format(
                    version=plan["plan_version"], protected=plan["protected_tasks"]
                )
        return result

    @app.post("/api/v1/goal-previews/{preview_id}/accept")
    def goal_accept(
        preview_id: str,
        body: ConfirmInput,
        if_match: Version,
        request: Request,
        db=Db,
        principal=Identity,
    ):
        result = growth.accept_goal(
            db, principal, preview_id, int(if_match.strip('"')), request.state.trace_id
        )
        result["replan_preview"] = learning.auto_preview(
            db, principal, "GoalChanged", request.state.trace_id
        )
        db.commit()
        return result
